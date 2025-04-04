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
from flask import abort # For permission errors
from models import db, User, CareerPath, Milestone, Step, Resource, UserStepStatus, PortfolioItem
from forms import RegistrationForm, LoginForm, OnboardingForm, PortfolioItemForm


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
    return render_template('home.html', is_homepage=True)

# --- Combined Dashboard Route ---
@app.route('/dashboard')
@login_required
def dashboard():
    if not current_user.onboarding_complete:
        flash('Please complete your profile information to get started.', 'info')
        return redirect(url_for('onboarding'))
        

    target_path = current_user.target_career_path
    milestones = []
    completed_step_ids = set()
    milestone_progress = {}
    timeline_estimate = "Timeline unavailable"
    # Overall Metrics
    total_steps_in_path = 0
    total_completed_steps = 0
    overall_percent_complete = 0

    if target_path:
        # Fetch milestones ordered by sequence
        milestones = Milestone.query.filter_by(career_path_id=target_path.id).order_by(Milestone.sequence).all()

        # Get all Step IDs for the user's current path
        current_path_step_ids_query = Step.query.join(Milestone).filter(
            Milestone.career_path_id == target_path.id
        ).with_entities(Step.id)
        current_path_step_ids = {step_id for step_id, in current_path_step_ids_query.all()}
        total_steps_in_path = len(current_path_step_ids)

        if current_path_step_ids: # Only proceed if the path has steps
            # Get IDs of completed steps for the user within this path
            completed_statuses_query = UserStepStatus.query.filter(
                UserStepStatus.user_id == current_user.id,
                UserStepStatus.status == 'completed',
                UserStepStatus.step_id.in_(current_path_step_ids)
            ).with_entities(UserStepStatus.step_id)
            completed_step_ids = {step_id for step_id, in completed_statuses_query.all()}
            total_completed_steps = len(completed_step_ids)

            # Calculate Overall Progress Percentage
            if total_steps_in_path > 0:
                overall_percent_complete = round((total_completed_steps / total_steps_in_path) * 100)

            # Calculate Per-Milestone Progress
            for milestone in milestones:
                # Use .all() to get steps if needed for intersection, or count() directly
                # Using count() might be slightly more efficient if steps list isn't needed elsewhere
                total_steps_in_milestone = Step.query.filter_by(milestone_id=milestone.id).count()
                # milestone.steps.count() # Alternative if lazy='dynamic'

                if total_steps_in_milestone > 0:
                    # Get step IDs for this specific milestone to intersect with completed IDs
                    milestone_step_ids_query = Step.query.filter_by(milestone_id=milestone.id).with_entities(Step.id)
                    milestone_step_ids = {step_id for step_id, in milestone_step_ids_query.all()}
                    completed_in_milestone = len(completed_step_ids.intersection(milestone_step_ids))
                    percent_complete = round((completed_in_milestone / total_steps_in_milestone) * 100)
                    milestone_progress[milestone.id] = {
                        'completed': completed_in_milestone,
                        'total': total_steps_in_milestone,
                        'percent': percent_complete
                    }
                else:
                     milestone_progress[milestone.id] = {'completed': 0, 'total': 0, 'percent': 0}

            # --- Timeline Estimation Logic ---
            if current_user.time_commitment:
                try:
                    commitment_str = current_user.time_commitment
                    avg_mins_per_week = 0
                    if commitment_str == '<5 hrs': avg_mins_per_week = 2.5 * 60
                    elif commitment_str == '5-10 hrs': avg_mins_per_week = 7.5 * 60
                    elif commitment_str == '10-15 hrs': avg_mins_per_week = 12.5 * 60
                    elif commitment_str == '15+ hrs': avg_mins_per_week = 20 * 60
                    else: avg_mins_per_week = 10 * 60 # Default guess

                    if avg_mins_per_week > 0:
                        remaining_step_ids = current_path_step_ids - completed_step_ids
                        if remaining_step_ids:
                            remaining_steps_data = Step.query.filter(
                                Step.id.in_(remaining_step_ids)
                            ).with_entities(Step.estimated_time_minutes).all()
                            total_remaining_minutes = sum(time or 0 for time, in remaining_steps_data)

                            if total_remaining_minutes > 0:
                                estimated_weeks = round(total_remaining_minutes / avg_mins_per_week)
                                timeline_estimate = f"~ {estimated_weeks} weeks remaining (estimated)"
                            else:
                                timeline_estimate = "Remaining steps have no time estimate."
                        else: # All steps in path are completed
                             timeline_estimate = "Congratulations! All steps complete."
                    else: # avg_mins_per_week is 0
                        timeline_estimate = "Set weekly time commitment for estimate."
                except Exception as e:
                    print(f"Error calculating timeline: {e}")
                    timeline_estimate = "Could not calculate timeline."
            else: # No time commitment set
                timeline_estimate = "Set weekly time commitment for estimate."
        else: # Path has no steps
             timeline_estimate = "No steps defined for this path."


    # --- Render the dashboard template ---
    return render_template('dashboard.html',
                           user=current_user,
                           path=target_path,
                           milestones=milestones,
                           timeline_estimate=timeline_estimate,
                           completed_step_ids=completed_step_ids,
                           milestone_progress=milestone_progress,
                           total_steps_in_path=total_steps_in_path,
                           total_completed_steps=total_completed_steps,
                           overall_percent_complete=overall_percent_complete)
                           is_homepage=False)



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

    return render_template('onboarding.html', title='Complete Your Profile', form=form, is_homepage=False)

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


# --- Portfolio Routes ---

# Utility function to get portfolio upload path
def get_portfolio_upload_path(filename):
    portfolio_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    # Ensure portfolio subdirectory exists
    os.makedirs(portfolio_dir, exist_ok=True)
    return os.path.join(portfolio_dir, filename)

@app.route('/portfolio')
@login_required
def portfolio():
    """Displays the user's portfolio items."""
    items = PortfolioItem.query.filter_by(user_id=current_user.id).order_by(PortfolioItem.created_at.desc()).all()
    return render_template('portfolio.html', title='My Portfolio', portfolio_items=items, is_homepage=False)

@app.route('/portfolio/add', methods=['GET', 'POST'])
@login_required
def add_portfolio_item():
    """Handles adding a new portfolio item."""
    form = PortfolioItemForm()
    if form.validate_on_submit():
        link_url = form.link_url.data
        file_filename_to_save = None # Filename to store in DB

        # Handle file upload
        if form.item_file.data:
            file = form.item_file.data
            base_filename = secure_filename(file.filename)
            unique_id = uuid.uuid4().hex
            name, ext = os.path.splitext(base_filename)
            ext = ext.lower()
            name = name[:100] # Limit base name length
            file_filename_to_save = f"user_{current_user.id}_portfolio_{unique_id}{ext}"
            try:
                file_path = get_portfolio_upload_path(file_filename_to_save)
                file.save(file_path)
                print(f"Portfolio file saved to: {file_path}")
            except Exception as e:
                print(f"Error saving portfolio file: {e}")
                flash('Error uploading file. Please try again.', 'danger')
                # Decide if we should proceed without the file or return
                file_filename_to_save = None # Don't save filename if save failed
                # Optionally return here: return render_template(...)

        # Create new PortfolioItem object
        new_item = PortfolioItem(
            user_id=current_user.id,
            title=form.title.data,
            description=form.description.data,
            item_type=form.item_type.data,
            link_url=link_url if link_url else None, # Store None if empty
            file_filename=file_filename_to_save # Store the unique filename or None
            # Add associated_step_id / milestone_id later if needed
        )
        try:
            db.session.add(new_item)
            db.session.commit()
            flash('Portfolio item added successfully!', 'success')
            return redirect(url_for('portfolio'))
        except Exception as e:
            db.session.rollback()
            print(f"Error adding portfolio item to DB: {e}")
            flash('Error saving portfolio item. Please try again.', 'danger')

    # Render the form template (used for both add and edit)
    return render_template('add_edit_portfolio_item.html', title='Add Portfolio Item', form=form, is_edit=False, is_homepage=False)


@app.route('/portfolio/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_portfolio_item(item_id):
    """Handles editing an existing portfolio item."""
    item = PortfolioItem.query.get_or_404(item_id)
    # Check ownership
    if item.user_id != current_user.id:
        abort(403) # Forbidden

    form = PortfolioItemForm(obj=item) # Pre-populate form with item data on GET

    if form.validate_on_submit():
        file_filename_to_save = item.file_filename # Keep existing file by default
        old_filename_to_delete = None

        # Handle optional file upload (replaces existing if new file provided)
        if form.item_file.data:
            # Store old filename before assigning new one
            if item.file_filename:
                old_filename_to_delete = item.file_filename

            file = form.item_file.data
            base_filename = secure_filename(file.filename)
            unique_id = uuid.uuid4().hex
            name, ext = os.path.splitext(base_filename)
            ext = ext.lower()
            name = name[:100]
            file_filename_to_save = f"user_{current_user.id}_portfolio_{unique_id}{ext}"
            try:
                file_path = get_portfolio_upload_path(file_filename_to_save)
                file.save(file_path)
                print(f"Updated portfolio file saved to: {file_path}")
            except Exception as e:
                print(f"Error saving updated portfolio file: {e}")
                flash('Error uploading new file. Please try again.', 'danger')
                file_filename_to_save = item.file_filename # Revert to old filename if save fails
                old_filename_to_delete = None # Don't delete old file if new one failed

        # Update item fields
        item.title = form.title.data
        item.description = form.description.data
        item.item_type = form.item_type.data
        item.link_url = form.link_url.data if form.link_url.data else None
        item.file_filename = file_filename_to_save

        try:
            db.session.commit()
            flash('Portfolio item updated successfully!', 'success')

            # Delete old file AFTER commit is successful
            if old_filename_to_delete:
                try:
                    old_file_path = get_portfolio_upload_path(old_filename_to_delete)
                    if os.path.exists(old_file_path):
                         os.remove(old_file_path)
                         print(f"Deleted old portfolio file: {old_file_path}")
                except OSError as e:
                     print(f"Error deleting old portfolio file {old_file_path}: {e}")
                     # Optionally flash a warning message about failure to delete old file

            return redirect(url_for('portfolio'))
        except Exception as e:
            db.session.rollback()
            print(f"Error updating portfolio item {item_id}: {e}")
            flash('Error updating portfolio item. Please try again.', 'danger')

    # Pass item only needed to display current file info if editing
    return render_template('add_edit_portfolio_item.html', title='Edit Portfolio Item', form=form, is_edit=True, item=item, is_homepage=False)


@app.route('/portfolio/<int:item_id>/delete', methods=['POST']) # Use POST for deletion
@login_required
def delete_portfolio_item(item_id):
    """Handles deleting a portfolio item."""
    item = PortfolioItem.query.get_or_404(item_id)
    # Check ownership
    if item.user_id != current_user.id:
        abort(403) # Forbidden

    filename_to_delete = item.file_filename # Get filename before deleting DB record

    try:
        db.session.delete(item)
        db.session.commit()

        # Delete associated file AFTER successful DB deletion
        if filename_to_delete:
            try:
                file_path = get_portfolio_upload_path(filename_to_delete)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"Deleted portfolio file: {file_path}")
            except OSError as e:
                print(f"Error deleting portfolio file {file_path}: {e}")
                # Optionally flash a warning message

        flash('Portfolio item deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting portfolio item {item_id}: {e}")
        flash('Error deleting portfolio item. Please try again.', 'danger')

    return redirect(url_for('portfolio'))


# --- NEW Profile Route ---
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """Displays user profile and handles updates."""
    # Use the EditProfileForm, pre-populate with current user data on GET
    form = EditProfileForm(obj=current_user)
    # Manually set the target path on GET if using obj= doesn't work perfectly for QuerySelectField
    # if request.method == 'GET' and current_user.target_career_path:
    #     form.target_career_path.data = current_user.target_career_path

    if form.validate_on_submit():
        try:
            # --- Handle CV Upload (similar to onboarding) ---
            cv_filename_to_save = current_user.cv_filename # Keep existing by default
            if form.cv_upload.data:
                file = form.cv_upload.data
                base_filename = secure_filename(file.filename)
                unique_id = uuid.uuid4().hex
                name, ext = os.path.splitext(base_filename)
                ext = ext.lower()
                name = name[:100]
                cv_filename_to_save = f"user_{current_user.id}_{unique_id}{ext}"
                # Save to main upload folder for now (or create 'uploads/cvs/')
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], cv_filename_to_save)

                # Delete old CV file if it exists and name changes
                if current_user.cv_filename and current_user.cv_filename != cv_filename_to_save:
                   old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.cv_filename)
                   if os.path.exists(old_path):
                       try:
                           os.remove(old_path)
                           print(f"Removed old CV during profile update: {old_path}")
                       except OSError as e:
                           print(f"Error removing old CV {old_path}: {e}")

                # Save the new file
                file.save(file_path)
                print(f"New CV saved via profile to: {file_path}")

            # --- Update User Object ---
            # Note: Don't update email or password here unless intended
            current_user.first_name = form.first_name.data
            current_user.last_name = form.last_name.data
            current_user.target_career_path = form.target_career_path.data
            current_user.current_role = form.current_role.data
            current_user.employment_status = form.employment_status.data
            current_user.time_commitment = form.time_commitment.data
            current_user.interests = form.interests.data
            current_user.learning_style = form.learning_style.data if form.learning_style.data else None
            current_user.cv_filename = cv_filename_to_save # Update with new/existing/None

            # Ensure onboarding is marked complete if profile is updated
            current_user.onboarding_complete = True

            db.session.commit()
            flash('Your profile has been updated successfully!', 'success')
            return redirect(url_for('profile')) # Redirect back to profile page

        except Exception as e:
             db.session.rollback()
             print(f"Error during profile update: {e}")
             flash('An error occurred while updating your profile. Please try again.', 'danger')

    # For GET requests, the form is already populated via obj=current_user
    # If obj= doesn't work perfectly for selects/queryselects, populate manually:
    # elif request.method == 'GET':
    #    form.first_name.data = current_user.first_name
    #    # ... and so on for other fields ...
    #    if current_user.target_career_path:
    #         form.target_career_path.data = current_user.target_career_path


    return render_template('profile.html',
                           title='Edit Profile',
                           form=form,
                           is_homepage=False) # Use sidebar navigation

# --- Main execution ---
if __name__ == '__main__':
    # Ensure the upload folder exists
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    # Use port from environment variable if available (Railway sets PORT)
    portfolio_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    if not os.path.exists(portfolio_upload_dir):
        os.makedirs(portfolio_upload_dir)
    port = int(os.environ.get('PORT', 5000))
    # Set host to '0.0.0.0' to be accessible externally if needed, not just 127.0.0.1
    app.run(debug=True, host='0.0.0.0', port=port) # Enable debug mode for development
