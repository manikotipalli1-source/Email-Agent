from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import json
import os
import schedule
import time
from datetime import datetime
import pickle

model = pickle.load(open('email_model.pkl', 'rb'))


SCOPES = ['https://mail.google.com/']

# Whitelist - add senders you always want to keep
WHITELIST = [    'google.com',
    'github.com',
    'microsoft.com',
    'nta.ac.in',
    'ntaonline.in',
    'josaa.nic.in',
    'flipkart.com',
    'realme.com',
    'vercel.com',
    'instagram.com',
    'facebook.com',
    'twitter.com',
    'ippb.gov.in',
    'perplexity.ai',
    'scrimba.com',
    'kaggle.com',
    'sbigeneral.in',

    # Add emails here like: 'important@example.com'
]

def authenticate_gmail():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file('C:/Users/manik/OneDrive/Documents/AI Automation/Email Cleaner/ECA-5/credentials.json', SCOPES)
        creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


def get_last_cleanup_time():
    if os.path.exists('last_cleanup.json'):
        with open('last_cleanup.json', 'r') as f:
            data = json.load(f)
            return data.get('last_cleanup')
    return None


def save_last_cleanup_time():
    with open('last_cleanup.json', 'w') as f:
        json.dump({'last_cleanup': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, f)


def get_emails(service, after_timestamp=None, max_results=500):
    query = ''
    if after_timestamp:
        # Convert timestamp to Gmail query format
        dt = datetime.strptime(after_timestamp, '%Y-%m-%d %H:%M:%S')
        query = f'after:{int(dt.timestamp())}'
    
    results = service.users().messages().list(
        userId='me', 
        maxResults=max_results,
        q=query
    ).execute()
    
    messages = results.get('messages', [])
    emails = []
    for message in messages:
        msg = service.users().messages().get(userId='me', id=message['id'], format='full').execute()
        headers = msg['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
        snippet = msg.get('snippet', '')
        emails.append({
            'id': message['id'],
            'subject': subject,
            'sender': sender,
            'snippet': snippet
        })
    return emails


def is_whitelisted(sender):
    for whitelisted in WHITELIST:
        if whitelisted.lower() in sender.lower():
            return True
    return False

def classify_email(subject, sender, snippet):
    # Combine subject and snippet as input text
    text = subject + ' ' + snippet
    prediction = model.predict([text])[0]
    return prediction == 1  # 1 = Spam, 0 = Keep

def save_deleted_email(subject, sender, snippet):
    data = []
    if os.path.exists('deleted_emails.json'):
        with open('deleted_emails.json', 'r') as f:
            data = json.load(f)
    data.append({
        'subject': subject,
        'sender': sender,
        'snippet': snippet,
        'deleted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    with open('deleted_emails.json', 'w') as f:
        json.dump(data, f, indent=2)

def save_kept_email(subject, sender, snippet):
    data = []
    if os.path.exists('kept_emails.json'):
        with open('kept_emails.json', 'r') as f:
            data = json.load(f)
    data.append({
        'subject': subject,
        'sender': sender,
        'snippet': snippet,
        'kept_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    with open('kept_emails.json', 'w') as f:
        json.dump(data, f, indent=2)      

def delete_email(service, email_id):
    service.users().messages().trash(userId='me', id=email_id).execute()

def cleanup_inbox(service):
    print(f"\nStarting inbox cleanup at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    last_cleanup = get_last_cleanup_time()
    
    if last_cleanup:
        print(f"Fetching emails since last cleanup: {last_cleanup}")
        emails = get_emails(service, after_timestamp=last_cleanup)
    else:
        print("First run - fetching entire inbox...")
        emails = get_emails(service)
    
    print(f"Found {len(emails)} emails to process...")
    
    deleted = 0
    kept = 0
    
    for email in emails:
        if is_whitelisted(email['sender']):
            print(f"Whitelisted - Kept: {email['subject']}")
            kept += 1
            continue
        
        is_junk = classify_email(email['subject'], email['sender'], email['snippet'])
        
        if is_junk:
            delete_email(service, email['id'])
            save_deleted_email(email['subject'], email['sender'], email['snippet'])
            print(f"Deleted: {email['subject']}")
            deleted += 1
        else:
            save_kept_email(email['subject'], email['sender'], email['snippet'])
            print(f"Kept: {email['subject']}")
            kept += 1
    
    # Save cleanup time after successful run
    save_last_cleanup_time()
    print(f"\nCleanup complete. Deleted: {deleted}, Kept: {kept}")

def main():
    service = authenticate_gmail()
    print("Gmail authenticated successfully!")
    
    # Run cleanup immediately on start
    cleanup_inbox(service)
    
    # Schedule daily cleanup at 8am
    schedule.every().day.at("08:00").do(cleanup_inbox, service)
    
    print("\nScheduler running. Next cleanup at 8:00 AM daily.")
    while True:
        schedule.run_pending()
        time.sleep(60)

main()