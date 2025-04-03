# main.py (Temporary Minimal Version)
from flask import Flask
import os
print("--- Starting Minimal App ---")

app = Flask(__name__)
print("Minimal app created.")

@app.route('/')
def minimal_home():
    print("Minimal home route hit!")
    return "Minimal App OK"

print("Minimal routes defined. App setup seemingly complete.")

# Note: No database, login, bcrypt, csrf needed for this test
# Note: The if __name__ == '__main__' block is not needed for Gunicorn
