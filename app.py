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

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Database setup
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///emailagent.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Mail configuration
resend.api_key = os.environ.get('RESEND_API_KEY')
s = URLSafeTimedSerializer(app.secret_key)

# Load trained model
model = pickle.load(open('email_model.pkl', 'rb'))

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

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

@app.route('/run_cleanup', methods=['POST'])
def run_cleanup():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    # For now return dummy data - we'll connect real cleanup next
    return jsonify({'deleted': 0, 'kept': 0})

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

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        user = User.query.filter_by(email=email).first()
        if user:
            token = s.dumps(email, salt='password-reset')
            reset_url = url_for('reset_password', token=token, _external=True)
            resend.Emails.send({
                "from": "onboarding@resend.dev",
                "to": email,
                "subject": "Reset your Email Agent password",
                "text": f"Click this link to reset your password: {reset_url}\n\nThis link expires in 1 hour."
            })
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)