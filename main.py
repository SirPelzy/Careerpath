import os
import uuid
from flask import Flask, render_template, redirect, url_for, flash, request
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from dotenv import load_dotenv
import datetime
from flask import render_template, redirect, url_for, flash, request # Ensure all are imported
from flask_login import login_user, logout_user, current_user, login_required # Ensure all are imported
from models import db, User, CareerPath # Ensure db and User are imported
from forms import RegistrationForm, LoginForm # Remove OnboardingForm for now, import the others


# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

@app.context_processor
def inject_now():
    return {'now': datetime.datetime.utcnow}

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
    return render_template('home.html')

# --- Dashboard Route (ensure login_required and onboarding check) ---
@app.route('/dashboard')
@login_required
def dashboard():
    if not current_user.onboarding_complete:
        flash('Please complete your profile information to get started.', 'info')
        return redirect(url_for('onboarding'))
    # --- Placeholder dashboard logic ---
    user_path = None # Replace with actual path fetching later
    # Make sure to create dashboard.html template later
    # return render_template('dashboard.html', user=current_user, path=user_path)
    return f"Welcome to your Dashboard, {current_user.first_name}! (Onboarding Complete: {current_user.onboarding_complete})" # Placeholder

# --- Authentication Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard')) # Redirect if already logged in
    form = RegistrationForm()
    if form.validate_on_submit():
        # Check again in route just in case (though form validator should catch it)
        existing_user = User.query.filter_by(email=form.email.data.lower()).first()
        if existing_user:
             flash('That email is already registered. Please log in.', 'warning')
             return redirect(url_for('login'))

        user = User(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data.lower() # Store email in lowercase
        )
        user.set_password(form.password.data) # Hash password
        db.session.add(user)
        db.session.commit()
        flash('Your account has been created! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', title='Register', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard')) # Redirect if already logged in
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            user.last_login = datetime.datetime.utcnow() # Update last login time
            db.session.commit()
            next_page = request.args.get('next')
            # Security: Validate next_page URL if provided
            if next_page and not next_page.startswith('/'):
                 next_page = None # Discard potentially malicious URL
            flash('Login Successful!', 'success')
            # Redirect logic: Onboarding or Dashboard
            if not user.onboarding_complete:
                 return redirect(url_for('onboarding'))
            else:
                 return redirect(next_page or url_for('dashboard')) # Redirect to next or dashboard
        else:
            flash('Login Unsuccessful. Please check email and password.', 'danger')
    return render_template('login.html', title='Login', form=form)

@app.route('/logout')
@login_required # Ensure user must be logged in to log out
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))


# --- Onboarding Route (Placeholder - will implement next) ---
@app.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    if current_user.onboarding_complete:
         return redirect(url_for('dashboard'))
    # form = OnboardingForm() # We will create and use this later
    # if form.validate_on_submit():
    #     # Update user profile fields
    #     # current_user.onboarding_complete = True
    #     # db.session.commit()
    #     # flash('Profile updated!', 'success')
    #     # return redirect(url_for('dashboard'))
    # return render_template('onboarding.html', title='Complete Profile', form=form) # Template to be created
    return f"Onboarding page for {current_user.first_name}. (Complete: {current_user.onboarding_complete})" # Placeholder


if __name__ == '__main__':
    # Ensure the upload folder exists
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True) # Enable debug mode for development
