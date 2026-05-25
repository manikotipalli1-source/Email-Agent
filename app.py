# 1. All imports
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pickle
import os
import json
from datetime import datetime
import resend
from itsdangerous import URLSafeTimedSerializer
import os
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# 2. App configuration
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# 3. Database setup
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///emailagent.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# 4. Resend and serializer
resend.api_key = os.environ.get('RESEND_API_KEY')
s = URLSafeTimedSerializer(app.secret_key)

# 5. Load trained model
model = pickle.load(open('email_model.pkl', 'rb'))

# 6. Gmail OAuth config 
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

# 7. Database models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    gmail_token = db.Column(db.Text, nullable=True)
    gmail_email = db.Column(db.String(150), nullable=True)

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
    return render_template('dashboard.html', email=session['user_email'])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

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
    emails = []
    if os.path.exists('deleted_emails.json'):
        deleted = json.load(open('deleted_emails.json'))
        for e in deleted[-10:]:
            emails.append({'subject': e['subject'], 'sender': e['sender'], 'label': 'Deleted'})
    if os.path.exists('kept_emails.json'):
        kept = json.load(open('kept_emails.json'))
        for e in kept[-10:]:
            emails.append({'subject': e['subject'], 'sender': e['sender'], 'label': 'Kept'})
    return jsonify({'emails': emails})

@app.route('/forgot_password', methods=['GET', 'POST'])
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
    flow = get_gmail_flow()
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    token_data = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    user = User.query.get(session['user_id'])
    user.gmail_token = json.dumps(token_data)
    service = build('gmail', 'v1', credentials=credentials)
    profile = service.users().getProfile(userId='me').execute()
    user.gmail_email = profile['emailAddress']
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/run_cleanup', methods=['POST'])
def run_cleanup():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = User.query.get(session['user_id'])
    if not user.gmail_token:
        return jsonify({'error': 'Gmail not connected'}), 400
    try:
        token_data = json.loads(user.gmail_token)
        creds = Credentials(
            token=token_data['token'],
            refresh_token=token_data['refresh_token'],
            token_uri=token_data['token_uri'],
            client_id=token_data['client_id'],
            client_secret=token_data['client_secret'],
            scopes=token_data['scopes']
        )
        service = build('gmail', 'v1', credentials=creds)
        results = service.users().messages().list(
            userId='me', maxResults=50
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
            text = subject + ' ' + snippet
            prediction = model.predict([text])[0]
            if prediction == 1:
                service.users().messages().trash(userId='me', id=message['id']).execute()
                deleted += 1
            else:
                kept += 1
        user.gmail_token = json.dumps({
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        })
        db.session.commit()
        return jsonify({'deleted': deleted, 'kept': kept})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)