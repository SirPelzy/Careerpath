import os
import uuid
import datetime
from werkzeug.utils import secure_filename # To sanitize filenames
from flask import Flask, render_template, redirect, url_for, flash, request
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from dotenv import load_dotenv

# Import Models (Ensure all required models are listed)
from models import db, User, CareerPath, Milestone, Step, Resource, UserStepStatus # <-- Added UserStepStatus

# Import Forms
from forms import RegistrationForm, LoginForm, OnboardingForm


# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# --- Configuration ---
# Use environment variable for SECRET_KEY in production
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a-very-secure-fallback-key-34567')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
if not app.config['SQLALCHEMY_DATABASE_URI']:
    raise ValueError("No DATABASE_URL set for Flask application")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
# Increased max file size slightly, adjust as needed
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 # 10 MB limit for uploads

# --- Initialize Extensions ---
csrf = CSRFProtect(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'
db.init_app(app) # Initialize SQLAlchemy with the app context

# --- Context Processor for Jinja ---
@app.context_processor
def inject_now():
    return {'now': datetime.datetime.utcnow()} # Added parentheses

# --- User Loader for Flask-Login ---
@login_manager.user_loader
def load_user(user_id):
    """Loads user object for Flask-Login."""
    # Ensure user_id is converted to int
    return User.query.get(int(user_id))

# --- Routes ---
@app.route('/')
def home():
    return render_template('home.html')

# --- Dashboard Route ---
@app.route('/dashboard')
@login_required
def dashboard():
    if not current_user.onboarding_complete:
        flash('Please complete your profile information to get started.', 'info')
        return redirect(url_for('onboarding'))

    # Fetch user's path and milestones
    target_path = current_user.target_career_path
    milestones = []
    if target_path:
        milestones = Milestone.query.filter_by(career_path_id=target_path.id).order_by(Milestone.sequence).all()
        # Note: Accessing milestone.steps within the template will trigger lazy loading

    # --- Timeline Estimation Logic (Example - will refine later) ---
    timeline_estimate = "Timeline calculation coming soon..."
    if target_path and current_user.time_commitment:
        try:
            commitment_str = current_user.time_commitment
            avg_mins_per_week = 0
            if commitment_str == '<5 hrs': avg_mins_per_week = 2.5 * 60
            elif commitment_str == '5-10 hrs': avg_mins_per_week = 7.5 * 60
            elif commitment_str == '10-15 hrs': avg_mins_per_week = 12.5 * 60
            elif commitment_str == '15+ hrs': avg_mins_per_week = 20 * 60 # Estimate 20 for 15+
            else: avg_mins_per_week = 10 * 60 # Default guess

            if avg_mins_per_week > 0:
                completed_step_ids = {status.step_id for status in UserStepStatus.query.filter_by(user_id=current_user.id, status='completed').all()}

                all_steps_query = Step.query.join(Milestone).filter(Milestone.career_path_id == target_path.id)
                # Correct way to filter out completed steps using notin_
                remaining_steps = all_steps_query.filter(Step.id.notin_(completed_step_ids)).all()

                total_remaining_minutes = sum(step.estimated_time_minutes or 0 for step in remaining_steps)

                if total_remaining_minutes > 0:
                     estimated_weeks = round(total_remaining_minutes / avg_mins_per_week)
                     timeline_estimate = f"~ {estimated_weeks} weeks remaining (estimated)"
                else:
                     timeline_estimate = "All steps have estimated time 0 or are complete."
            else:
                timeline_estimate = "Set weekly time commitment for estimate."

        except Exception as e:
            print(f"Error calculating timeline: {e}")
            timeline_estimate = "Could not calculate timeline."

    return render_template('dashboard.html', # Use the new template file
                           user=current_user,
                           path=target_path,
                           milestones=milestones,
                           timeline_estimate=timeline_estimate)


# --- Authentication Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = RegistrationForm()
    if form.validate_on_submit():
        existing_user = User.query.filter_by(email=form.email.data.lower()).first()
        if existing_user:
            flash('That email is already registered. Please log in.', 'warning')
            return redirect(url_for('login'))
        try:
            user = User(
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                email=form.email.data.lower()
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash('Your account has been created! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            print(f"Error during registration: {e}")
            flash('An error occurred during registration. Please try again.', 'danger')
    return render_template('register.html', title='Register', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            user.last_login = datetime.datetime.utcnow()
            # Need to commit session after updating last_login
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error updating last_login: {e}")
                # Decide if this should prevent login - probably not

            next_page = request.args.get('next')
            # Security check for open redirect
            if next_page and not (next_page.startswith('/') or next_page.startswith(request.host_url)):
                 next_page = None
            flash('Login Successful!', 'success')
            if not user.onboarding_complete:
                 return redirect(url_for('onboarding'))
            else:
                 return redirect(next_page or url_for('dashboard'))
        else:
            flash('Login Unsuccessful. Please check email and password.', 'danger')
    return render_template('login.html', title='Login', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))

# --- Onboarding Route Implementation ---
@app.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    if current_user.onboarding_complete:
         return redirect(url_for('dashboard'))

    form = OnboardingForm()

    if form.validate_on_submit():
        try:
            # Handle CV Upload
            cv_filename_to_save = current_user.cv_filename # Keep existing if not uploading new
            if form.cv_upload.data:
                file = form.cv_upload.data
                base_filename = secure_filename(file.filename)
                unique_id = uuid.uuid4().hex
                name, ext = os.path.splitext(base_filename)
                # Ensure extension is lower case for consistency
                ext = ext.lower()
                name = name[:100] # Limit base name length
                cv_filename_to_save = f"user_{current_user.id}_{unique_id}{ext}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], cv_filename_to_save)

                # Optional: Delete old CV file
                if current_user.cv_filename and current_user.cv_filename != cv_filename_to_save:
                   old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.cv_filename)
                   if os.path.exists(old_path):
                       try:
                           os.remove(old_path)
                           print(f"Removed old CV: {old_path}")
                       except OSError as e:
                           print(f"Error removing old CV {old_path}: {e}")

                # Save the new file
                file.save(file_path)
                print(f"CV saved to: {file_path}")

            # Update user object
            current_user.target_career_path = form.target_career_path.data
            current_user.current_role = form.current_role.data
            current_user.employment_status = form.employment_status.data
            current_user.time_commitment = form.time_commitment.data
            current_user.interests = form.interests.data
            current_user.learning_style = form.learning_style.data if form.learning_style.data else None
            current_user.cv_filename = cv_filename_to_save # Update with new or keep old

            # Mark onboarding as complete
            current_user.onboarding_complete = True

            db.session.commit()
            flash('Your profile has been updated successfully!', 'success')
            return redirect(url_for('dashboard'))

        except Exception as e:
             db.session.rollback()
             print(f"Error during onboarding save: {e}") # Log the specific error
             flash('An error occurred while saving your profile. Please try again.', 'danger')

    return render_template('onboarding.html', title='Complete Your Profile', form=form)

# --- Route to Toggle Step Completion Status ---
@app.route('/path/step/<int:step_id>/toggle', methods=['POST'])
@login_required
def toggle_step_status(step_id):
    """Marks a step as complete or incomplete for the current user."""
    step = Step.query.get_or_404(step_id)
    # Optional: Check if step belongs to user's path? Maybe not needed if URL isn't guessable easily.

    # Find existing status or create a new one
    user_status = UserStepStatus.query.filter_by(user_id=current_user.id, step_id=step.id).first()

    try:
        if user_status:
            # Toggle status
            if user_status.status == 'completed':
                user_status.status = 'not_started'
                user_status.completed_at = None
                flash(f'Step "{step.name}" marked as not started.', 'info')
            else:
                user_status.status = 'completed'
                user_status.completed_at = datetime.datetime.utcnow()
                flash(f'Step "{step.name}" marked as completed!', 'success')
        else:
            # Create new status record as completed
            user_status = UserStepStatus(
                user_id=current_user.id,
                step_id=step.id,
                status='completed',
                completed_at=datetime.datetime.utcnow()
            )
            db.session.add(user_status)
            flash(f'Step "{step.name}" marked as completed!', 'success')

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        print(f"Error updating step status for user {current_user.id}, step {step_id}: {e}")
        flash('An error occurred while updating step status.', 'danger')

    # Redirect back to the dashboard where the path is displayed
    return redirect(url_for('dashboard'))

# --- Main execution ---
if __name__ == '__main__':
    # Ensure the upload folder exists
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    # Use port from environment variable if available (Railway sets PORT)
    port = int(os.environ.get('PORT', 5000))
    # Set host to '0.0.0.0' to be accessible externally if needed, not just 127.0.0.1
    app.run(debug=True, host='0.0.0.0', port=port) # Enable debug mode for development
