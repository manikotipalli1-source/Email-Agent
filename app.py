from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import pickle
import os
import json
import resend
from itsdangerous import URLSafeTimedSerializer
from datetime import datetime

app = Flask(__name__)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key_here')

# Database setup
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///emailagent.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Resend email setup
resend.api_key = os.environ.get('RESEND_API_KEY')
s = URLSafeTimedSerializer(app.secret_key)

# Load trained model
model = pickle.load(open('email_model.pkl', 'rb'))

# Gmail OAuth config
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

def get_gmail_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get('GOOGLE_CLIENT_ID'),
                "client_secret": os.environ.get('GOOGLE_CLIENT_SECRET'),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [os.environ.get('GOOGLE_REDIRECT_URI')]
            }
        },
        scopes=SCOPES,
        redirect_uri=os.environ.get('GOOGLE_REDIRECT_URI')
    )

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    gmail_token = db.Column(db.Text, nullable=True)
    gmail_email = db.Column(db.String(150), nullable=True)
    whitelist = db.Column(db.Text, nullable=True, default='[]')
    max_emails = db.Column(db.Integer, default=50)

class EmailActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(300), nullable=True)
    sender = db.Column(db.String(200), nullable=True)
    action = db.Column(db.String(10), nullable=False)  # 'deleted' or 'kept'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)    

# Routes
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['user_email'] = user.email
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid email or password')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        confirm = request.form['confirm_password']
        if password != confirm:
            return render_template('signup.html', error='Passwords do not match')
        if User.query.filter_by(email=email).first():
            return render_template('signup.html', error='Email already registered')
        user = User(email=email, password=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    gmail_connected = user.gmail_email is not None
    return render_template('dashboard.html',
                         email=session['user_email'],
                         gmail_connected=gmail_connected,
                         gmail_email=user.gmail_email)
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/connect-gmail')
def connect_gmail():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    flow = get_gmail_flow()
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['oauth_state'] = state
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    try:
        # Fix for HTTPS redirect
        auth_response = request.url.replace('http://', 'https://')
        flow = get_gmail_flow()
        flow.fetch_token(authorization_response=auth_response)
        credentials = flow.credentials
        token_data = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': list(credentials.scopes) if credentials.scopes else []
        }
        user = db.session.get(User, session['user_id'])
        if not user:
            return redirect(url_for('login'))
        user.gmail_token = json.dumps(token_data)
        service = build('gmail', 'v1', credentials=credentials)
        profile = service.users().getProfile(userId='me').execute()
        user.gmail_email = profile['emailAddress']
        db.session.commit()
        print(f"Gmail connected for user: {user.gmail_email}")
        return redirect(url_for('dashboard'))
    except Exception as e:
        print(f"OAuth error: {e}")
        return redirect(url_for('dashboard'))
    
@app.route('/run_cleanup', methods=['POST'])
def run_cleanup():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = db.session.get(User, session['user_id'])
    if not user or not user.gmail_token:
        return jsonify({'error': 'Gmail not connected'}), 400
    try:
        whitelist = json.loads(user.whitelist or '[]')
        token_data = json.loads(user.gmail_token)
        creds = Credentials(
            token=token_data['token'],
            refresh_token=token_data.get('refresh_token'),
            token_uri=token_data['token_uri'],
            client_id=token_data['client_id'],
            client_secret=token_data['client_secret'],
            scopes=token_data.get('scopes', [])
        )
        service = build('gmail', 'v1', credentials=creds)
        results = service.users().messages().list(
            userId='me', maxResults=user.max_emails
        ).execute()
        messages = results.get('messages', [])
        deleted = 0
        kept = 0
        for message in messages:
            msg = service.users().messages().get(
                userId='me', id=message['id'], format='full'
            ).execute()
            headers = msg['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            snippet = msg.get('snippet', '')
            # Check whitelist first
            if any(w.lower() in sender.lower() for w in whitelist):
                kept += 1
                continue
            text = subject + ' ' + snippet
            prediction = model.predict([text])[0]
            if prediction == 1:
                service.users().messages().trash(userId='me', id=message['id']).execute()
                activity = EmailActivity(user_id=user.id, subject=subject, sender=sender, action='deleted')
                db.session.add(activity)
                deleted += 1
            else:
                activity = EmailActivity(user_id=user.id, subject=subject, sender=sender, action='kept')
                db.session.add(activity)
                kept += 1

        user.gmail_token = json.dumps({
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': list(creds.scopes) if creds.scopes else []
        })
        db.session.commit()
        return jsonify({'deleted': deleted, 'kept': kept})
    except Exception as e:
        print(f"Cleanup error: {e}")
        return jsonify({'error': str(e)}), 500
    

@app.route('/classify', methods=['POST'])
def classify():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json
    text = data.get('subject', '') + ' ' + data.get('snippet', '')
    prediction = model.predict([text])[0]
    result = 'Spam' if prediction == 1 else 'Ham'
    return jsonify({'result': result})

@app.route('/activity')
def activity():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    activities = EmailActivity.query.filter_by(
        user_id=session['user_id']
    ).order_by(EmailActivity.timestamp.desc()).limit(20).all()
    emails = [{'subject': a.subject, 'sender': a.sender, 'label': a.action.capitalize()} for a in activities]
    return jsonify({'emails': emails})

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        user = User.query.filter_by(email=email).first()
        if user:
            try:
                token = s.dumps(email, salt='password-reset')
                reset_url = url_for('reset_password', token=token, _external=True)
                resend.Emails.send({
                    "from": "onboarding@resend.dev",
                    "to": email,
                    "subject": "Reset your Email Agent password",
                    "text": f"Click this link to reset your password: {reset_url}\n\nThis link expires in 1 hour."
                })
            except Exception as e:
                print(f"Email error: {e}")
        return render_template('forgot_password.html', sent=True)
    return render_template('forgot_password.html', sent=False)

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = s.loads(token, salt='password-reset', max_age=3600)
    except:
        return render_template('reset_password.html', error='Link expired or invalid.')
    if request.method == 'POST':
        password = request.form['password']
        confirm = request.form['confirm_password']
        if password != confirm:
            return render_template('reset_password.html', error='Passwords do not match.')
        user = User.query.filter_by(email=email).first()
        user.password = generate_password_hash(password)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('reset_password.html', error=None)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add_whitelist':
            sender = request.form.get('sender', '').strip()
            if sender:
                whitelist = json.loads(user.whitelist or '[]')
                if sender not in whitelist:
                    whitelist.append(sender)
                    user.whitelist = json.dumps(whitelist)
                    db.session.commit()
        
        elif action == 'remove_whitelist':
            sender = request.form.get('sender', '').strip()
            whitelist = json.loads(user.whitelist or '[]')
            if sender in whitelist:
                whitelist.remove(sender)
                user.whitelist = json.dumps(whitelist)
                db.session.commit()
        
        elif action == 'update_max':
            max_emails = int(request.form.get('max_emails', 50))
            user.max_emails = max_emails
            db.session.commit()
        
        return redirect(url_for('settings'))
    
    whitelist = json.loads(user.whitelist or '[]')
    return render_template('settings.html', 
                         email=session['user_email'],
                         whitelist=whitelist,
                         max_emails=user.max_emails)

@app.route('/disconnect-gmail')
def disconnect_gmail():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if user:
        user.gmail_token = None
        user.gmail_email = None
        db.session.commit()
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

