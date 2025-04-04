import os
import uuid
from flask import Flask, render_template, redirect, url_for, flash, request
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from dotenv import load_dotenv
# Import models and forms later when created
# from models import db, User, CareerPath
# from forms import RegistrationForm, LoginForm, OnboardingForm

# Generate a unique key once and put it here or in .env
# You could generate one using: python -c "import uuid; print(uuid.uuid4())"
INIT_DB_SECRET_KEY = os.environ.get('INIT_DB_SECRET_KEY', 'replace-this-with-a-very-secret-key')

@app.route(f'/admin/init-db/{INIT_DB_SECRET_KEY}') # Use a secret path
def init_database():
    """Temporary route to initialize the database."""
    print("Attempting to initialize database...")
    try:
        with app.app_context():
            db.create_all()
            print("Database tables created (or already exist).")

            # Optional: Pre-populate Career Paths (Check if they exist first)
            if not CareerPath.query.first():
                print("Populating initial Career Paths...")
                paths = [
                    CareerPath(name="Data Analysis / Analytics", description="Focuses on interpreting data, finding insights, and visualization."),
                    CareerPath(name="UX/UI Design", description="Focuses on user experience and interface design for digital products."),
                    CareerPath(name="Cybersecurity", description="Focuses on protecting computer systems and networks from threats."),
                    CareerPath(name="Software Engineering", description="Focuses on designing, developing, and maintaining software systems.")
                ]
                db.session.add_all(paths)
                db.session.commit()
                print("Career Paths added.")
            else:
                 print("Career Paths already exist.")

        return "Database initialization attempted successfully!", 200
    except Exception as e:
        print(f"Error during DB initialization: {e}")
        return f"Error during DB initialization: {e}", 500

# !!! REMEMBER TO REMOVE THIS ROUTE AFTER USE AND REDEPLOY !!!

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'fallback_secret_key_for_development')
# Ensure DATABASE_URL is set in your .env file (e.g., postgresql://user:password@host:port/database)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
if not app.config['SQLALCHEMY_DATABASE_URI']:
    raise ValueError("No DATABASE_URL set for Flask application")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Configuration for file uploads (adjust path as needed)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16 MB limit for uploads

# Initialize Extensions
# db = SQLAlchemy(app) # Initialize db here once models are imported
csrf = CSRFProtect(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login' # Redirect users to 'login' view if @login_required fails
login_manager.login_message_category = 'info' # Flash message category

# --- Database Models (Import and initialize db) ---
# Need to define models in models.py first
# Then import and initialize db here
from models import db, User, CareerPath # Assuming models.py is created
db.init_app(app) # Initialize SQLAlchemy with the app context

# --- User Loader for Flask-Login ---
@login_manager.user_loader
def load_user(user_id):
    """Loads user object for Flask-Login."""
    return User.query.get(int(user_id))

# --- Routes ---
@app.route('/')
def home():
    """Serves the home page."""
    return render_template('home.html') # We'll create this template later

@app.route('/dashboard')
@login_required
def dashboard():
    """Serves the main user dashboard after login."""
    # Check if onboarding is complete, redirect if not
    if not current_user.onboarding_complete:
        flash('Please complete your profile information to get started.', 'info')
        return redirect(url_for('onboarding'))

    # --- Placeholder for dashboard logic ---
    # Fetch user's path, milestones, etc.
    user_path = None # Example: Fetch UserPath associated with current_user
    return render_template('dashboard.html', user=current_user, path=user_path) # Template to be created

# --- Authentication Routes (Placeholder Stubs) ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    # Implementation will go here using RegistrationForm
    return "Register Page Placeholder" # Placeholder

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Implementation will go here using LoginForm
    return "Login Page Placeholder" # Placeholder

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))

# --- Onboarding Route (Placeholder Stub) ---
@app.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    # Implementation will go here using OnboardingForm
    # Redirect if already completed
    if current_user.onboarding_complete:
         return redirect(url_for('dashboard'))
    return "Onboarding Page Placeholder" # Placeholder


if __name__ == '__main__':
    # Ensure the upload folder exists
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True) # Enable debug mode for development
