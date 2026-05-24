import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import pickle

# Load dataset
print("Loading dataset...")
df = pd.read_csv('spam_Emails_data.csv')
print(f"Total emails: {len(df)}")
print(df['label'].value_counts())

# Prepare data
df = df.dropna()
X = df['text']
y = (df['label'] == 'Spam').astype(int)  # 1=Spam, 0=Not Spam

# Split into training and testing sets
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)
print(f"\nTraining on {len(X_train)} emails, testing on {len(X_test)} emails")

# Build and train the model
print("\nTraining model...")
model = Pipeline([
    ('tfidf', TfidfVectorizer(max_features=5000)),
    ('clf', MultinomialNB())
])
model.fit(X_train, y_train)

# Test accuracy
predictions = model.predict(X_test)
accuracy = accuracy_score(y_test, predictions)
print(f"\nAccuracy: {accuracy * 100:.2f}%")
print(classification_report(y_test, predictions, target_names=['Not Spam', 'Spam']))

# Save the trained model
pickle.dump(model, open('email_model.pkl', 'wb'))
print("\nModel saved as email_model.pkl")