import os
import uuid
import datetime
from werkzeug.utils import secure_filename # To sanitize filenames
from flask import Flask, render_template, redirect, url_for, flash, request, abort
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from dotenv import load_dotenv

# Import Models
from models import db, User, CareerPath, Milestone, Step, Resource, UserStepStatus, PortfolioItem

# Import Forms (Make sure EditProfileForm is imported)
from forms import RegistrationForm, LoginForm, OnboardingForm, PortfolioItemForm, EditProfileForm

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# --- Configuration ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a-very-secure-fallback-key-34567') # Use env var ideally
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
if not app.config['SQLALCHEMY_DATABASE_URI']:
    raise ValueError("No DATABASE_URL set for Flask application")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
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
    return {'now': datetime.datetime.utcnow()}

# --- User Loader for Flask-Login ---
@login_manager.user_loader
def load_user(user_id):
    """Loads user object for Flask-Login."""
    return User.query.get(int(user_id))

# --- Routes ---
@app.route('/')
def home():
    # Pass is_homepage=True for the homepage
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
                total_steps_in_milestone = Step.query.filter_by(milestone_id=milestone.id).count()
                if total_steps_in_milestone > 0:
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
                           overall_percent_complete=overall_percent_complete,
                           is_homepage=False) # Pass flag for layout

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
    # Use is_homepage=True for consistency with login page style? Or define separate base?
    # Let's assume login/register use the homepage style for now.
    return render_template('register.html', title='Register', form=form, is_homepage=True)

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
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error updating last_login: {e}")

            next_page = request.args.get('next')
            if next_page and not (next_page.startswith('/') or next_page.startswith(request.host_url)):
                 next_page = None
            flash('Login Successful!', 'success')
            if not user.onboarding_complete:
                 return redirect(url_for('onboarding'))
            else:
                 return redirect(next_page or url_for('dashboard'))
        else:
            flash('Login Unsuccessful. Please check email and password.', 'danger')
    # Assume login uses homepage style
    return render_template('login.html', title='Login', form=form, is_homepage=True)

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
                ext = ext.lower()
                name = name[:100] # Limit base name length
                cv_filename_to_save = f"user_{current_user.id}_{unique_id}{ext}"
                # Save to main upload folder
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

            current_user.onboarding_complete = True

            db.session.commit()
            flash('Your profile has been updated successfully!', 'success')
            return redirect(url_for('dashboard'))

        except Exception as e:
             db.session.rollback()
             print(f"Error during onboarding save: {e}")
             flash('An error occurred while saving your profile. Please try again.', 'danger')

    return render_template('onboarding.html', title='Complete Your Profile', form=form, is_homepage=False) # Use sidebar

# --- Route to Toggle Step Completion Status ---
@app.route('/path/step/<int:step_id>/toggle', methods=['POST'])
@login_required
def toggle_step_status(step_id):
    """Marks a step as complete or incomplete for the current user."""
    step = Step.query.get_or_404(step_id)

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

    return redirect(url_for('dashboard'))


# --- Portfolio Routes ---

# Utility function to get portfolio upload path
def get_portfolio_upload_path(filename):
    portfolio_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    os.makedirs(portfolio_dir, exist_ok=True) # Ensure portfolio subdirectory exists
    return os.path.join(portfolio_dir, filename)

@app.route('/portfolio')
@login_required
def portfolio():
    """Displays the user's portfolio items."""
    items = PortfolioItem.query.filter_by(user_id=current_user.id).order_by(PortfolioItem.created_at.desc()).all()
    return render_template('portfolio.html', title='My Portfolio', portfolio_items=items, is_homepage=False) # Use sidebar

@app.route('/portfolio/add', methods=['GET', 'POST'])
@login_required
def add_portfolio_item():
    """Handles adding a new portfolio item."""
    form = PortfolioItemForm()
    if form.validate_on_submit():
        link_url = form.link_url.data
        file_filename_to_save = None # Filename to store in DB

        if form.item_file.data:
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
                print(f"Portfolio file saved to: {file_path}")
            except Exception as e:
                print(f"Error saving portfolio file: {e}")
                flash('Error uploading file. Please try again.', 'danger')
                file_filename_to_save = None

        new_item = PortfolioItem(
            user_id=current_user.id,
            title=form.title.data,
            description=form.description.data,
            item_type=form.item_type.data,
            link_url=link_url if link_url else None,
            file_filename=file_filename_to_save
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

    return render_template('add_edit_portfolio_item.html', title='Add Portfolio Item', form=form, is_edit=False, is_homepage=False) # Use sidebar


@app.route('/portfolio/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_portfolio_item(item_id):
    """Handles editing an existing portfolio item."""
    item = PortfolioItem.query.get_or_404(item_id)
    if item.user_id != current_user.id:
        abort(403)

    form = PortfolioItemForm(obj=item) # Pre-populate form

    if form.validate_on_submit():
        file_filename_to_save = item.file_filename # Keep existing file by default
        old_filename_to_delete = None

        if form.item_file.data:
            if item.file_filename: # Mark old file for deletion
                old_filename_to_delete = item.file_filename

            # Save new file
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
                file_filename_to_save = item.file_filename # Revert to old name
                old_filename_to_delete = None # Don't delete old file

        # Update item fields in DB object
        item.title = form.title.data
        item.description = form.description.data
        item.item_type = form.item_type.data
        item.link_url = form.link_url.data if form.link_url.data else None
        item.file_filename = file_filename_to_save

        try:
            db.session.commit() # Commit changes to item
            flash('Portfolio item updated successfully!', 'success')

            # Delete old file AFTER commit succeeds
            if old_filename_to_delete:
                try:
                    old_file_path = get_portfolio_upload_path(old_filename_to_delete)
                    if os.path.exists(old_file_path):
                        os.remove(old_file_path)
                        print(f"Deleted old portfolio file: {old_file_path}")
                except OSError as e:
                     print(f"Error deleting old portfolio file {old_file_path}: {e}")

            return redirect(url_for('portfolio'))
        except Exception as e:
            db.session.rollback()
            print(f"Error updating portfolio item {item_id}: {e}")
            flash('Error updating portfolio item. Please try again.', 'danger')

    return render_template('add_edit_portfolio_item.html', title='Edit Portfolio Item', form=form, is_edit=True, item=item, is_homepage=False) # Use sidebar


@app.route('/portfolio/<int:item_id>/delete', methods=['POST'])
@login_required
def delete_portfolio_item(item_id):
    """Handles deleting a portfolio item."""
    item = PortfolioItem.query.get_or_404(item_id)
    if item.user_id != current_user.id:
        abort(403)

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

        flash('Portfolio item deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting portfolio item {item_id}: {e}")
        flash('Error deleting portfolio item. Please try again.', 'danger')

    return redirect(url_for('portfolio'))


# --- Profile Route ---
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """Displays user profile and handles updates."""
    # Ensure EditProfileForm is imported from forms
    form = EditProfileForm(obj=current_user)

    # Manual pre-population for QuerySelectField might be needed if obj= fails
    # Typically obj=current_user works if relationships are set up correctly
    if request.method == 'GET' and current_user.target_career_path:
        form.target_career_path.data = current_user.target_career_path # Ensure this works

    if form.validate_on_submit():
        try:
            # --- Handle CV Upload (using main uploads folder) ---
            cv_filename_to_save = current_user.cv_filename
            if form.cv_upload.data:
                file = form.cv_upload.data
                base_filename = secure_filename(file.filename)
                unique_id = uuid.uuid4().hex
                name, ext = os.path.splitext(base_filename)
                ext = ext.lower()
                name = name[:100]
                cv_filename_to_save = f"user_{current_user.id}_{unique_id}{ext}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], cv_filename_to_save)

                # Delete old CV file
                if current_user.cv_filename and current_user.cv_filename != cv_filename_to_save:
                   old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.cv_filename)
                   if os.path.exists(old_path):
                       try:
                           os.remove(old_path)
                           print(f"Removed old CV during profile update: {old_path}")
                       except OSError as e:
                           print(f"Error removing old CV {old_path}: {e}")

                file.save(file_path)
                print(f"New CV saved via profile to: {file_path}")

            # --- Update User Object ---
            current_user.first_name = form.first_name.data
            current_user.last_name = form.last_name.data
            current_user.target_career_path = form.target_career_path.data
            current_user.current_role = form.current_role.data
            current_user.employment_status = form.employment_status.data
            current_user.time_commitment = form.time_commitment.data
            current_user.interests = form.interests.data
            current_user.learning_style = form.learning_style.data if form.learning_style.data else None
            current_user.cv_filename = cv_filename_to_save

            current_user.onboarding_complete = True # Ensure this remains true

            db.session.commit()
            flash('Your profile has been updated successfully!', 'success')
            return redirect(url_for('profile')) # Redirect back to profile page

        except Exception as e:
             db.session.rollback()
             print(f"Error during profile update: {e}")
             flash('An error occurred while updating your profile. Please try again.', 'danger')

    # --- Correct indentation for the final return statement ---
    return render_template('profile.html',
                           title='Edit Profile',
                           form=form,
                           is_homepage=False) # Use sidebar navigation

@app.route(f'/admin/init-db/{INIT_DB_SECRET_KEY}')
def init_database():
    """Temporary route to initialize the database and seed path data."""
    print("Attempting database initialization and seeding...")
    try:
        with app.app_context():
            # Create all tables that don't exist
            db.create_all()
            print("Database tables checked/created.")

            # --- Seed Initial Career Paths (if needed) ---
            # ... (Keep existing CareerPath seeding logic here) ...

            # --- Seed Data Analytics Path (if needed) ---
            # ... (Keep existing Data Analytics seeding logic here) ...


            # --- <<< ADD THIS NEW BLOCK FOR UX/UI SEEDING >>> ---
            print("Checking for UX/UI Design path seeding...")
            uxui_path = CareerPath.query.filter_by(name="UX/UI Design").first()

            # Check if milestones for this specific path already exist
            if uxui_path and not Milestone.query.filter_by(career_path_id=uxui_path.id).first():
                print(f"Seeding path for '{uxui_path.name}'...")

                # Use lists to collect objects for bulk add if preferred, or add individually
                resources_to_add_ux = [] # Separate list for this path's resources

                # --- Milestone 1: Introduction to UX/UI ---
                m1_ux = Milestone(name="Introduction to UX/UI Design", sequence=10, career_path_id=uxui_path.id); db.session.add(m1_ux); db.session.flush()
                s1_1_ux = Step(name="Understand UX vs UI", sequence=10, estimated_time_minutes=60, milestone_id=m1_ux.id)
                s1_2_ux = Step(name="Explore UX/UI Career Paths & Roles", sequence=20, estimated_time_minutes=90, milestone_id=m1_ux.id)
                s1_3_ux = Step(name="Learn the Design Thinking Process", sequence=30, estimated_time_minutes=120, milestone_id=m1_ux.id)
                db.session.add_all([s1_1_ux, s1_2_ux, s1_3_ux]); db.session.flush()
                # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="UX vs UI Explained (CareerFoundry)", url="#placeholder_uxui_cf_article", resource_type="Article", step_id=s1_1_ux.id))
                resources_to_add_ux.append(Resource(name="Google UX Design Certificate Intro (Coursera)", url="#placeholder_uxui_google_intro", resource_type="Course", step_id=s1_2_ux.id))
                resources_to_add_ux.append(Resource(name="Design Thinking Overview (IDEO)", url="#placeholder_uxui_ideo_dt", resource_type="Resource", step_id=s1_3_ux.id))

                # --- Milestone 2: Core Design Principles ---
                m2_ux = Milestone(name="Core Design Principles", sequence=20, career_path_id=uxui_path.id); db.session.add(m2_ux); db.session.flush()
                s2_1_ux = Step(name="Visual Hierarchy & Layout", sequence=10, estimated_time_minutes=120, milestone_id=m2_ux.id)
                s2_2_ux = Step(name="Color Theory Basics", sequence=20, estimated_time_minutes=90, milestone_id=m2_ux.id)
                s2_3_ux = Step(name="Typography Fundamentals", sequence=30, estimated_time_minutes=90, milestone_id=m2_ux.id)
                s2_4_ux = Step(name="Usability Heuristics (Nielsen's 10)", sequence=40, estimated_time_minutes=120, milestone_id=m2_ux.id)
                db.session.add_all([s2_1_ux, s2_2_ux, s2_3_ux, s2_4_ux]); db.session.flush()
                # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="Laws of UX (Website)", url="#placeholder_uxui_laws_ux", resource_type="Resource", step_id=s2_1_ux.id))
                resources_to_add_ux.append(Resource(name="Color Theory (Interaction Design Foundation)", url="#placeholder_uxui_idf_color", resource_type="Article", step_id=s2_2_ux.id))
                resources_to_add_ux.append(Resource(name="Typography Guide (Material Design)", url="#placeholder_uxui_material_type", resource_type="Guide", step_id=s2_3_ux.id))
                resources_to_add_ux.append(Resource(name="10 Usability Heuristics (NN/g)", url="#placeholder_uxui_nng_heuristics", resource_type="Article", step_id=s2_4_ux.id))

                # --- Milestone 3: User Research Fundamentals ---
                m3_ux = Milestone(name="User Research Fundamentals", sequence=30, career_path_id=uxui_path.id); db.session.add(m3_ux); db.session.flush()
                s3_1_ux = Step(name="Creating User Personas", sequence=10, estimated_time_minutes=120, milestone_id=m3_ux.id)
                s3_2_ux = Step(name="Conducting User Interviews", sequence=20, estimated_time_minutes=180, milestone_id=m3_ux.id)
                s3_3_ux = Step(name="Survey Design Basics", sequence=30, estimated_time_minutes=90, milestone_id=m3_ux.id)
                s3_4_ux = Step(name="Introduction to Usability Testing", sequence=40, estimated_time_minutes=180, milestone_id=m3_ux.id)
                db.session.add_all([s3_1_ux, s3_2_ux, s3_3_ux, s3_4_ux]); db.session.flush()
                # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="Personas Guide (Interaction Design Foundation)", url="#placeholder_uxui_idf_personas", resource_type="Article", step_id=s3_1_ux.id))
                resources_to_add_ux.append(Resource(name="User Interview Guide (NN/g)", url="#placeholder_uxui_nng_interviews", resource_type="Article", step_id=s3_2_ux.id))
                resources_to_add_ux.append(Resource(name="Survey Guide (SurveyMonkey)", url="#placeholder_uxui_sm_surveys", resource_type="Guide", step_id=s3_3_ux.id))
                resources_to_add_ux.append(Resource(name="Usability Testing 101 (NN/g)", url="#placeholder_uxui_nng_testing", resource_type="Article", step_id=s3_4_ux.id))

                # --- Milestone 4: IA & User Flows ---
                m4_ux = Milestone(name="Information Architecture & User Flows", sequence=40, career_path_id=uxui_path.id); db.session.add(m4_ux); db.session.flush()
                s4_1_ux = Step(name="Information Architecture Basics", sequence=10, estimated_time_minutes=120, milestone_id=m4_ux.id)
                s4_2_ux = Step(name="Creating Sitemaps", sequence=20, estimated_time_minutes=90, milestone_id=m4_ux.id)
                s4_3_ux = Step(name="Mapping User Flows", sequence=30, estimated_time_minutes=180, milestone_id=m4_ux.id)
                s4_4_ux = Step(name="Introduction to Card Sorting", sequence=40, estimated_time_minutes=60, milestone_id=m4_ux.id)
                db.session.add_all([s4_1_ux, s4_2_ux, s4_3_ux, s4_4_ux]); db.session.flush()
                # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="Complete Beginner's Guide to IA (UX Booth)", url="#placeholder_uxui_uxbooth_ia", resource_type="Article", step_id=s4_1_ux.id))
                resources_to_add_ux.append(Resource(name="Sitemaps Tutorial (Figma)", url="#placeholder_uxui_figma_sitemaps", resource_type="Tutorial", step_id=s4_2_ux.id))
                resources_to_add_ux.append(Resource(name="User Flow Guide (Adobe XD)", url="#placeholder_uxui_adobe_flows", resource_type="Guide", step_id=s4_3_ux.id))
                resources_to_add_ux.append(Resource(name="Card Sorting Intro (NN/g)", url="#placeholder_uxui_nng_cardsort", resource_type="Article", step_id=s4_4_ux.id))

                # --- Milestone 5: Wireframing & Prototyping ---
                m5_ux = Milestone(name="Wireframing & Prototyping", sequence=50, career_path_id=uxui_path.id); db.session.add(m5_ux); db.session.flush()
                s5_1_ux = Step(name="Understanding Wireframe Fidelity", sequence=10, estimated_time_minutes=60, milestone_id=m5_ux.id)
                s5_2_ux = Step(name="Creating Low-Fidelity Wireframes", sequence=20, estimated_time_minutes=180, milestone_id=m5_ux.id)
                s5_3_ux = Step(name="Building High-Fidelity Wireframes/Mockups", sequence=30, estimated_time_minutes=300, milestone_id=m5_ux.id)
                s5_4_ux = Step(name="Creating Interactive Prototypes", sequence=40, estimated_time_minutes=300, milestone_id=m5_ux.id)
                db.session.add_all([s5_1_ux, s5_2_ux, s5_3_ux, s5_4_ux]); db.session.flush()
                # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="Wireframing Guide (CareerFoundry)", url="#placeholder_uxui_cf_wireframe", resource_type="Guide", step_id=s5_1_ux.id))
                resources_to_add_ux.append(Resource(name="Low-Fi Wireframing Tools (Blog)", url="#placeholder_uxui_blog_lowfi", resource_type="Article", step_id=s5_2_ux.id))
                resources_to_add_ux.append(Resource(name="Hi-Fi Wireframing Tutorial (Figma YT)", url="#placeholder_uxui_figmayt_hifi", resource_type="Video", step_id=s5_3_ux.id))
                resources_to_add_ux.append(Resource(name="Prototyping in Figma (Figma Docs)", url="#placeholder_uxui_figmadocs_proto", resource_type="Documentation", step_id=s5_4_ux.id))

                # --- Milestone 6: Mastering Design Tools (Figma) ---
                m6_ux = Milestone(name="Mastering Design Tools (Figma Focus)", sequence=60, career_path_id=uxui_path.id); db.session.add(m6_ux); db.session.flush()
                s6_1_ux = Step(name="Figma Interface & Basics", sequence=10, estimated_time_minutes=240, milestone_id=m6_ux.id)
                s6_2_ux = Step(name="Using Auto Layout", sequence=20, estimated_time_minutes=180, milestone_id=m6_ux.id)
                s6_3_ux = Step(name="Working with Components & Variants", sequence=30, estimated_time_minutes=300, milestone_id=m6_ux.id)
                s6_4_ux = Step(name="Exploring Figma Plugins", sequence=40, estimated_time_minutes=60, milestone_id=m6_ux.id)
                s6_5_ux = Step(name="Practice Project in Figma", sequence=50, estimated_time_minutes=600, milestone_id=m6_ux.id)
                db.session.add_all([s6_1_ux, s6_2_ux, s6_3_ux, s6_4_ux, s6_5_ux]); db.session.flush()
                # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="Figma Beginners Tutorial (YT)", url="#placeholder_uxui_yt_figmabasic", resource_type="Video", step_id=s6_1_ux.id))
                resources_to_add_ux.append(Resource(name="Figma Auto Layout Guide (Figma)", url="#placeholder_uxui_figma_autolayout", resource_type="Guide", step_id=s6_2_ux.id))
                resources_to_add_ux.append(Resource(name="Figma Components Tutorial (YT)", url="#placeholder_uxui_yt_components", resource_type="Video", step_id=s6_3_ux.id))
                resources_to_add_ux.append(Resource(name="Top Figma Plugins (Blog)", url="#placeholder_uxui_blog_plugins", resource_type="Article", step_id=s6_4_ux.id))
                resources_to_add_ux.append(Resource(name="Design a Mobile App (YT Project)", url="#placeholder_uxui_yt_project", resource_type="Project", step_id=s6_5_ux.id))

                # --- Milestone 7: Visual Design & UI ---
                m7_ux = Milestone(name="Visual Design & UI Details", sequence=70, career_path_id=uxui_path.id); db.session.add(m7_ux); db.session.flush()
                s7_1_ux = Step(name="Creating Style Guides", sequence=10, estimated_time_minutes=120, milestone_id=m7_ux.id)
                s7_2_ux = Step(name="Introduction to Design Systems", sequence=20, estimated_time_minutes=180, milestone_id=m7_ux.id)
                s7_3_ux = Step(name="Web Accessibility Basics (WCAG)", sequence=30, estimated_time_minutes=240, milestone_id=m7_ux.id)
                s7_4_ux = Step(name="Using UI Kits & Iconography", sequence=40, estimated_time_minutes=120, milestone_id=m7_ux.id)
                db.session.add_all([s7_1_ux, s7_2_ux, s7_3_ux, s7_4_ux]); db.session.flush()
                # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="Style Guide Examples (Blog)", url="#placeholder_uxui_blog_styleguide", resource_type="Article", step_id=s7_1_ux.id))
                resources_to_add_ux.append(Resource(name="Design Systems Intro (InVision)", url="#placeholder_uxui_invision_ds", resource_type="Article", step_id=s7_2_ux.id))
                resources_to_add_ux.append(Resource(name="WCAG Overview (W3C)", url="#placeholder_uxui_w3c_wcag", resource_type="Documentation", step_id=s7_3_ux.id))
                resources_to_add_ux.append(Resource(name="Free UI Kits for Figma (Resource)", url="#placeholder_uxui_resource_uikits", resource_type="Resource", step_id=s7_4_ux.id))

                # --- Milestone 8: Building Your UX/UI Portfolio ---
                m8_ux = Milestone(name="Building Your UX/UI Portfolio", sequence=80, career_path_id=uxui_path.id); db.session.add(m8_ux); db.session.flush()
                s8_1_ux = Step(name="Selecting Portfolio Projects", sequence=10, estimated_time_minutes=60, milestone_id=m8_ux.id)
                s8_2_ux = Step(name="Structuring a UX Case Study", sequence=20, estimated_time_minutes=180, milestone_id=m8_ux.id)
                s8_3_ux = Step(name="Choosing a Portfolio Platform (Behance, Dribbble, etc.)", sequence=30, estimated_time_minutes=120, milestone_id=m8_ux.id)
                s8_4_ux = Step(name="Create & Refine 2-3 Case Studies", sequence=40, estimated_time_minutes=1200, milestone_id=m8_ux.id)
                db.session.add_all([s8_1_ux, s8_2_ux, s8_3_ux, s8_4_ux]); db.session.flush()
                # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="How to Choose Projects (Blog)", url="#placeholder_uxui_blog_projects", resource_type="Article", step_id=s8_1_ux.id))
                resources_to_add_ux.append(Resource(name="UX Case Study Guide (Medium)", url="#placeholder_uxui_medium_casestudy", resource_type="Article", step_id=s8_2_ux.id))
                resources_to_add_ux.append(Resource(name="Behance", url="https://www.behance.net/", resource_type="Platform", step_id=s8_3_ux.id))
                resources_to_add_ux.append(Resource(name="Dribbble", url="https://dribbble.com/", resource_type="Platform", step_id=s8_3_ux.id))

                # --- Milestone 9: Collaboration & Handoff ---
                m9_ux = Milestone(name="Collaboration & Handoff", sequence=90, career_path_id=uxui_path.id); db.session.add(m9_ux); db.session.flush()
                s9_1_ux = Step(name="Working Effectively with Developers", sequence=10, estimated_time_minutes=120, milestone_id=m9_ux.id)
                s9_2_ux = Step(name="Creating Design Specifications", sequence=20, estimated_time_minutes=90, milestone_id=m9_ux.id)
                s9_3_ux = Step(name="Using Handoff Features (Figma Inspect)", sequence=30, estimated_time_minutes=60, milestone_id=m9_ux.id)
                db.session.add_all([s9_1_ux, s9_2_ux, s9_3_ux]); db.session.flush()
                # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="Designer/Developer Collaboration (Abstract)", url="#placeholder_uxui_abstract_collab", resource_type="Article", step_id=s9_1_ux.id))
                resources_to_add_ux.append(Resource(name="Design Specs Guide (Zeplin Blog)", url="#placeholder_uxui_zeplin_specs", resource_type="Article", step_id=s9_2_ux.id))
                resources_to_add_ux.append(Resource(name="Figma Inspect Mode (Figma Docs)", url="#placeholder_uxui_figmadocs_inspect", resource_type="Documentation", step_id=s9_3_ux.id))

                # --- Milestone 10: Job Search & Interview Prep (UX/UI) ---
                m10_ux = Milestone(name="Job Search & Interview Prep (UX/UI)", sequence=100, career_path_id=uxui_path.id); db.session.add(m10_ux); db.session.flush()
                s10_1_ux = Step(name="Tailoring Your UX/UI Resume", sequence=10, estimated_time_minutes=120, milestone_id=m10_ux.id)
                s10_2_ux = Step(name="Preparing Your Portfolio for Review", sequence=20, estimated_time_minutes=180, milestone_id=m10_ux.id)
                s10_3_ux = Step(name="Common UX/UI Interview Questions", sequence=30, estimated_time_minutes=180, milestone_id=m10_ux.id)
                s10_4_ux = Step(name="Understanding Design Challenges & Whiteboard Tests", sequence=40, estimated_time_minutes=240, milestone_id=m10_ux.id)
                db.session.add_all([s10_1_ux, s10_2_ux, s10_3_ux, s10_4_ux]); db.session.flush()
                 # !!! REPLACE PLACEHOLDER URLs BELOW !!!
                resources_to_add_ux.append(Resource(name="UX Resume Tips (NN/g)", url="#placeholder_uxui_nng_resume", resource_type="Article", step_id=s10_1_ux.id))
                resources_to_add_ux.append(Resource(name="Portfolio Review Prep (Medium)", url="#placeholder_uxui_medium_portfolioreview", resource_type="Article", step_id=s10_2_ux.id))
                resources_to_add_ux.append(Resource(name="UX Interview Questions (Toptal)", url="#placeholder_uxui_toptal_interview", resource_type="Article", step_id=s10_3_ux.id))
                resources_to_add_ux.append(Resource(name="Whiteboard Challenge Guide (YT)", url="#placeholder_uxui_yt_whiteboard", resource_type="Video", step_id=s10_4_ux.id))

                # --- Add all collected resources for UX/UI ---
                if resources_to_add_ux:
                    db.session.add_all(resources_to_add_ux)

                db.session.commit() # Commit all additions for the UX/UI path
                print(f"Path '{uxui_path.name}' seeded successfully.")

            elif uxui_path:
                 print(f"Path '{uxui_path.name}' milestones already seem to exist. Skipping seeding.")
            else:
                 print("UX/UI Design career path not found in DB, skipping seeding.")

            # --- <<< END ADDED BLOCK FOR UX/UI SEEDING >>> ---


            flash("Database initialization and seeding check complete.", 'info')
            return redirect(url_for('home')) # Redirect home after completion

    except Exception as e:
        db.session.rollback() # Rollback on any error during the process
        print(f"Error during DB initialization/seeding: {e}") # Log the specific error
        flash(f"Error during DB initialization/seeding: {e}", 'danger')
        return redirect(url_for('home')) # Redirect home even on error


# --- Main execution ---
if __name__ == '__main__':
    # Ensure the main upload folder exists
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    # Ensure the portfolio subfolder exists
    portfolio_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    if not os.path.exists(portfolio_upload_dir):
        os.makedirs(portfolio_upload_dir)

    port = int(os.environ.get('PORT', 5000))
    # Set host to '0.0.0.0' to be accessible externally
    app.run(debug=True, host='0.0.0.0', port=port)
