# main.py
import os
from flask import Flask, render_template, redirect, url_for, flash # Added flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
# Import db instance created in models.py
from models import db
# Import User model later when defined: from models import User
# Import forms later when defined: from forms import RegistrationForm, LoginForm

# Load environment variables if using a .env file locally (optional)
# from dotenv import load_dotenv
# load_dotenv()

print("--- Starting App Setup ---")

app = Flask(__name__)

# --- Configuration ---
print("Loading Config...")
# Load SECRET_KEY from environment or use a default (change default for safety)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_dev_secret_key_123!@#')
# Load DATABASE_URL from environment for Railway/production
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres'):
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    print("Using PostgreSQL Database.")
else:
    # Fallback to SQLite for local Replit development if DATABASE_URL not set
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///careerpath.db' # Use a different DB name
    print("Using SQLite Database (Fallback).")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
print("Config Loaded.")

# --- Initialize Extensions ---
print("Initializing Extensions...")
# db defined in models.py, associate it with app
db.init_app(app)
print("DB Initialized.")
bcrypt = Bcrypt(app)
print("Bcrypt Initialized.")
login_manager = LoginManager(app)
login_manager.login_view = 'login' # Route name for the login page
login_manager.login_message_category = 'info' # Bootstrap class for flash message
print("LoginManager Initialized.")
csrf = CSRFProtect(app)
print("CSRFProtect Initialized.")
print("Extensions Initialized.")

# --- User Loader Function (Required by Flask-Login) ---
# Needs User model defined first
# @login_manager.user_loader
# def load_user(user_id):
#    from models import User # Import here or globally if no circularity issues
#    return User.query.get(int(user_id))
# print("User Loader Defined.") # Uncomment when User model and loader are ready


# !!!!!! TEMPORARY ROUTE FOR INITIAL DB SETUP - REMOVE IMMEDIATELY AFTER USE !!!!!!
# --- CHOOSE AND SET YOUR OWN UNIQUE SECRET PATH BELOW ---
# Replace 'initialize-careerpath-db-x7y3z1q9p0' with your own random string!
SECRET_DB_INIT_PATH = 'initialize-careerpath-db-x7y3z1q9p0'
# --- END CHOOSE SECRET PATH ---

@app.route(f'/{SECRET_DB_INIT_PATH}')
def temp_create_initial_tables():
    print(f"ACCESSING TEMPORARY DB INIT ROUTE: /{SECRET_DB_INIT_PATH}")
    try:
        # Ensure commands run within Flask's application context
        with app.app_context():
             print("App context active. Creating all tables based on models.py...")
             # db should be globally available from its import in main.py
             # from models import db # Can uncomment if needed, but likely not
             db.create_all() # Creates tables if they don't exist based on models.py
             print("db.create_all() command finished.")
        # Return a success message to the browser
        return f"OK: db.create_all() executed via /{SECRET_DB_INIT_PATH}. Database tables should be created. Remove this route NOW!", 200
    except Exception as e:
        print(f"ERROR during temporary DB init route: {e}")
         # Return an error message to the browser
        return f"Error during DB init: {e}", 500
# !!!!!! END OF TEMPORARY ROUTE - REMEMBER TO REMOVE !!!!!!



# --- Routes ---
print("Defining Routes...")
@app.route('/')
@app.route('/home')
def home():
    # Will add redirect for logged-in users later
    # from flask_login import current_user # Import when needed
    # if current_user.is_authenticated:
    #    return redirect(url_for('dashboard')) # Assuming a dashboard route later
    return render_template('home.html', title='Welcome')

# Add other routes (login, register, etc.) later

print("Routes Defined.")

# --- Main Execution ---
if __name__ == '__main__':
    # For development/Replit, create tables if they don't exist
    # Use app_context for database operations outside requests
    with app.app_context():
         print("Checking database tables...")
         # db.drop_all() # Use cautiously to reset
         db.create_all() # Creates tables based on models defined in models.py
         print("Database tables checked/created.")

    # Use PORT environment variable provided by Railway/hosting, default to 8081 for Replit
    port = int(os.environ.get('PORT', 8081))
    print(f"Starting Flask app on host 0.0.0.0, port {port}")
    app.run(host='0.0.0.0', port=port, debug=True) # Turn debug=False for production

print("--- App Setup Complete ---")
