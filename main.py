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

# Import Forms (Make sure EditProfileForm and RecommendationTestForm are imported)
from forms import RegistrationForm, LoginForm, OnboardingForm, PortfolioItemForm, EditProfileForm, RecommendationTestForm

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

# --- Combined Dashboard Route with Resource Personalization ---
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
    # Resource Personalization
    recommended_resource_ids = set()

    if target_path:
        # Fetch milestones ordered by sequence
        milestones = Milestone.query.filter_by(career_path_id=target_path.id).order_by(Milestone.sequence).all()

        # Fetch all Step IDs AND associated Resource details for the user's current path efficiently
        path_steps_resources_query = db.session.query(
                Step.id,
                Resource.id,
                Resource.name,
                Resource.resource_type
            ).select_from(Step).join(
                Milestone, Step.milestone_id == Milestone.id
            ).outerjoin( # Use outerjoin to include steps that might not have resources yet
                Resource, Step.id == Resource.step_id
            ).filter(
                Milestone.career_path_id == target_path.id
            )
        path_steps_resources = path_steps_resources_query.all()

        # Get unique Step IDs from the fetched data
        current_path_step_ids = {step_id for step_id, _, _, _ in path_steps_resources if step_id is not None}
        total_steps_in_path = len(current_path_step_ids)

        if current_path_step_ids: # Only proceed if the path has steps
            # Get IDs of completed steps (as before)
            completed_statuses_query = UserStepStatus.query.filter(
                UserStepStatus.user_id == current_user.id,
                UserStepStatus.status == 'completed',
                UserStepStatus.step_id.in_(current_path_step_ids)
            ).with_entities(UserStepStatus.step_id)
            completed_step_ids = {step_id for step_id, in completed_statuses_query.all()}
            total_completed_steps = len(completed_step_ids)

            # Calculate Overall Progress Percentage (as before)
            if total_steps_in_path > 0:
                overall_percent_complete = round((total_completed_steps / total_steps_in_path) * 100)

            # Calculate Per-Milestone Progress (as before)
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


            # --- Calculate Recommended Resource IDs ---
            user_style = current_user.learning_style
            user_interests_str = current_user.interests or ""
            # Basic keyword extraction
            interest_keywords = {
                keyword.strip().lower()
                for keyword in user_interests_str.replace(',', ' ').split()
                if len(keyword.strip()) > 2
            }

            # Define mapping from style to resource types
            style_to_type_map = {
                'Visual': ['Video', 'Project', 'Course', 'Guide', 'Platform'],
                'Auditory': ['Video', 'Course'],
                'Reading/Writing': ['Article', 'Documentation', 'Guide', 'Tutorial', 'Resource'],
                'Kinesthetic/Practical': ['Project', 'Practice', 'Course', 'Tool', 'Tutorial']
            }
            preferred_types = style_to_type_map.get(user_style, [])

            # Iterate through all resources fetched for the path
            for _step_id, resource_id, resource_name, resource_type in path_steps_resources:
                if resource_id is None: continue

                is_recommended = False
                # 1. Check Learning Style Match
                if resource_type and resource_type in preferred_types:
                    is_recommended = True

                # 2. Check Interest Match (if not already recommended)
                if not is_recommended and interest_keywords and resource_name:
                    resource_name_lower = resource_name.lower()
                    if any(keyword in resource_name_lower for keyword in interest_keywords):
                        is_recommended = True

                if is_recommended:
                    recommended_resource_ids.add(resource_id)
            # --- End Recommended Resource Calculation ---

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
                           recommended_resource_ids=recommended_resource_ids, # Pass the new set
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
    return render_template('login.html', title='Login', form=form, is_homepage=True)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))

# --- Onboarding Choice Route ---
@app.route('/onboarding')
@login_required
def onboarding():
    if current_user.onboarding_complete:
         return redirect(url_for('dashboard'))
    return render_template('onboarding_choice.html', title='Choose Your Start', is_homepage=False)

# --- Onboarding Form Route (Handles actual form) ---
@app.route('/onboarding/form', methods=['GET', 'POST'])
@login_required
def onboarding_form():
    if current_user.onboarding_complete:
         return redirect(url_for('dashboard'))

    # Ensure OnboardingForm is imported
    form = OnboardingForm()

    if request.method == 'GET':
        recommended_path_id = request.args.get('recommended_path_id', type=int)
        if recommended_path_id:
            recommended_path = CareerPath.query.get(recommended_path_id)
            if recommended_path:
                form.target_career_path.data = recommended_path
            else:
                flash('Invalid recommendation ID provided.', 'warning')

    if form.validate_on_submit():
        try:
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
                if current_user.cv_filename and current_user.cv_filename != cv_filename_to_save:
                   old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.cv_filename)
                   if os.path.exists(old_path):
                       try: os.remove(old_path)
                       except OSError as e: print(f"Error removing old CV {old_path}: {e}")
                file.save(file_path)
                print(f"CV saved to: {file_path}")

            current_user.target_career_path = form.target_career_path.data
            current_user.current_role = form.current_role.data
            current_user.employment_status = form.employment_status.data
            current_user.time_commitment = form.time_commitment.data
            current_user.interests = form.interests.data
            current_user.learning_style = form.learning_style.data if form.learning_style.data else None
            current_user.cv_filename = cv_filename_to_save
            current_user.onboarding_complete = True

            db.session.commit()
            flash('Your profile is set up! Welcome to your dashboard.', 'success')
            return redirect(url_for('dashboard'))

        except Exception as e:
             db.session.rollback()
             print(f"Error during onboarding form save: {e}")
             flash('An error occurred while saving your profile. Please try again.', 'danger')

    return render_template('onboarding_form.html',
                           title='Complete Your Profile',
                           form=form,
                           is_homepage=False)

# --- Recommendation Test Route ---
@app.route('/recommendation-test', methods=['GET', 'POST'])
@login_required
def recommendation_test():
    """Displays and processes the career recommendation test."""
    # Ensure RecommendationTestForm is imported
    form = RecommendationTestForm()
    if form.validate_on_submit():
        # Basic Rule-Based Scoring
        scores = {"Data Analysis / Analytics": 0, "UX/UI Design": 0, "Software Engineering": 0, "Cybersecurity": 0}
        answers = {
            'q1': form.q1_hobby.data,
            'q2': form.q2_approach.data,
            'q3': form.q3_reward.data,
            'q4': form.q4_feedback.data
        }

        # Q1 Scoring
        if answers['q1'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q1'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q1'] == 'C': scores["Software Engineering"] += 1
        elif answers['q1'] == 'D': scores["Cybersecurity"] += 1

        # Q2 Scoring
        if answers['q2'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q2'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q2'] == 'C': scores["Software Engineering"] += 1
        elif answers['q2'] == 'D': scores["Cybersecurity"] += 1

        # Q3 Scoring
        if answers['q3'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q3'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q3'] == 'C': scores["Software Engineering"] += 1
        elif answers['q3'] == 'D': scores["Cybersecurity"] += 1

        # Q4 Scoring
        if answers['q4'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q4'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q4'] == 'C': scores["Software Engineering"] += 1
        elif answers['q4'] == 'D': scores["Cybersecurity"] += 1

        # Determine highest score
        available_paths = {"Data Analysis / Analytics", "UX/UI Design"} # Add more as seeded
        filtered_scores = {path: score for path, score in scores.items() if path in available_paths and score > 0} # Only consider paths with score > 0

        if not filtered_scores:
             recommended_path_name = "Data Analysis / Analytics" # Default recommendation if no score > 0
             flash("Your answers didn't strongly match our current paths, defaulting recommendation.", "info")
        else:
             recommended_path_name = max(filtered_scores, key=filtered_scores.get)
             # Handle potential ties - simple approach: just takes the first max
             max_score = filtered_scores[recommended_path_name]
             tied_paths = [path for path, score in filtered_scores.items() if score == max_score]
             # For now, we just use the first one found by max()

        # Find the corresponding CareerPath object ID
        recommended_path = CareerPath.query.filter_by(name=recommended_path_name).first()

        if recommended_path:
            # Redirect to results page, passing recommendation info
            return redirect(url_for('recommendation_results',
                                    recommended_path_id=recommended_path.id,
                                    recommended_path_name=recommended_path.name))
        else:
            # Path not found in DB
            flash('Could not process recommendation (path not found). Please select a path manually.', 'warning')
            return redirect(url_for('onboarding_form'))

    # Render the test form on GET or if validation fails
    return render_template('recommendation_test.html',
                           title="Career Recommendation Test",
                           form=form,
                           is_homepage=False)

# --- Recommendation Results Route ---
@app.route('/recommendation-results') # GET only
@login_required
def recommendation_results():
    """Displays the recommendation results and next steps."""
    recommended_path_id = request.args.get('recommended_path_id', type=int)
    recommended_path_name = request.args.get('recommended_path_name')

    if not recommended_path_id or not recommended_path_name:
        flash('Recommendation results not found. Please try the test again.', 'warning')
        return redirect(url_for('recommendation_test'))

    # Render the REAL results template now
    return render_template('recommendation_results.html',
                            title="Your Recommendation",
                            recommended_path_id=recommended_path_id,
                            recommended_path_name=recommended_path_name,
                            is_homepage=False) # Use sidebar layout

# --- Route to Toggle Step Completion Status ---
@app.route('/path/step/<int:step_id>/toggle', methods=['POST'])
@login_required
def toggle_step_status(step_id):
    """Marks a step as complete or incomplete for the current user."""
    step = Step.query.get_or_404(step_id)

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
    # Ensure PortfolioItemForm is imported
    form = PortfolioItemForm()
    if form.validate_on_submit():
        link_url = form.link_url.data
        file_filename_to_save = None

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

    return render_template('add_edit_portfolio_item.html', title='Add Portfolio Item', form=form, is_edit=False, is_homepage=False)


@app.route('/portfolio/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_portfolio_item(item_id):
    """Handles editing an existing portfolio item."""
    item = PortfolioItem.query.get_or_404(item_id)
    if item.user_id != current_user.id:
        abort(403)

    form = PortfolioItemForm(obj=item)

    if form.validate_on_submit():
        file_filename_to_save = item.file_filename
        old_filename_to_delete = None

        if form.item_file.data:
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
                file_filename_to_save = item.file_filename
                old_filename_to_delete = None

        item.title = form.title.data
        item.description = form.description.data
        item.item_type = form.item_type.data
        item.link_url = form.link_url.data if form.link_url.data else None
        item.file_filename = file_filename_to_save

        try:
            db.session.commit()
            flash('Portfolio item updated successfully!', 'success')

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

    return render_template('add_edit_portfolio_item.html', title='Edit Portfolio Item', form=form, is_edit=True, item=item, is_homepage=False)


@app.route('/portfolio/<int:item_id>/delete', methods=['POST'])
@login_required
def delete_portfolio_item(item_id):
    """Handles deleting a portfolio item."""
    item = PortfolioItem.query.get_or_404(item_id)
    if item.user_id != current_user.id:
        abort(403)

    filename_to_delete = item.file_filename

    try:
        db.session.delete(item)
        db.session.commit()

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
    # Ensure EditProfileForm is imported
    form = EditProfileForm(obj=current_user)

    # Manual pre-population for QuerySelectField on GET
    if request.method == 'GET' and current_user.target_career_path:
        form.target_career_path.data = current_user.target_career_path

    if form.validate_on_submit():
        try:
            # Handle CV Upload
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

            # Update User Object
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

    # Correct indentation for the final return statement
    return render_template('profile.html',
                           title='Edit Profile',
                           form=form,
                           is_homepage=False) # Use sidebar navigation


# --- <<< TEMPORARY ADMIN ROUTE FOR DB INIT & SEEDING >>> ---
# !! IMPORTANT !! Remove or secure this route in production!
INIT_DB_SECRET_KEY = os.environ.get('INIT_DB_SECRET_KEY', 'replace-this-with-a-very-secret-key-9876')

@app.route(f'/admin/init-db/{INIT_DB_SECRET_KEY}')
def init_database():
    """Temporary route to initialize the database and seed path data."""
    print("Attempting database initialization and seeding...")
    try:
        with app.app_context():
            # --- <<< ADD CYBERSECURITY SEEDING BLOCK >>> ---
            print("Checking for Cybersecurity path seeding...")
            cyber_path = CareerPath.query.filter_by(name="Cybersecurity").first()

            if cyber_path and not Milestone.query.filter_by(career_path_id=cyber_path.id).first():
                print(f"Seeding path for '{cyber_path.name}'...")
                resources_to_add_cyber = []
                try: # Add inner try/except for seeding this specific path
                    # Milestone 1: IT & Networking Foundations
                    m1_cy = Milestone(name="Foundational IT & Networking Concepts", sequence=10, career_path_id=cyber_path.id); db.session.add(m1_cy); db.session.flush()
                    s1_1_cy = Step(name="Understand Computer Hardware & OS Basics", sequence=10, estimated_time_minutes=300, milestone_id=m1_cy.id)
                    s1_2_cy = Step(name="Learn the OSI & TCP/IP Models", sequence=20, estimated_time_minutes=240, milestone_id=m1_cy.id)
                    s1_3_cy = Step(name="IP Addressing (IPv4/IPv6) & Subnetting Basics", sequence=30, estimated_time_minutes=300, milestone_id=m1_cy.id)
                    s1_4_cy = Step(name="Common Network Protocols (HTTP, DNS, DHCP, etc.)", sequence=40, estimated_time_minutes=180, milestone_id=m1_cy.id)
                    db.session.add_all([s1_1_cy, s1_2_cy, s1_3_cy, s1_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="CompTIA IT Fundamentals (Exam Objectives)", url="#placeholder_cyber_itf_objectives", resource_type="Guide", step_id=s1_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="OSI Model Explained (Video)", url="#placeholder_cyber_yt_osi", resource_type="Video", step_id=s1_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="IP Addressing Tutorial (Website)", url="#placeholder_cyber_web_ip", resource_type="Tutorial", step_id=s1_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Network Protocols Overview (Article)", url="#placeholder_cyber_article_protocols", resource_type="Article", step_id=s1_4_cy.id))

                    # Milestone 2: Intro to Cybersecurity Principles
                    m2_cy = Milestone(name="Introduction to Cybersecurity Principles", sequence=20, career_path_id=cyber_path.id); db.session.add(m2_cy); db.session.flush()
                    s2_1_cy = Step(name="Learn the CIA Triad (Confidentiality, Integrity, Availability)", sequence=10, estimated_time_minutes=60, milestone_id=m2_cy.id)
                    s2_2_cy = Step(name="Identify Common Cyber Threats & Attack Vectors", sequence=20, estimated_time_minutes=120, milestone_id=m2_cy.id)
                    s2_3_cy = Step(name="Understand Basic Cryptography Concepts (Encryption, Hashing)", sequence=30, estimated_time_minutes=180, milestone_id=m2_cy.id)
                    s2_4_cy = Step(name="Develop a Security Mindset", sequence=40, estimated_time_minutes=60, milestone_id=m2_cy.id)
                    db.session.add_all([s2_1_cy, s2_2_cy, s2_3_cy, s2_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="CIA Triad Explained (Article)", url="#placeholder_cyber_article_cia", resource_type="Article", step_id=s2_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Common Cyber Threats (Cybrary - Placeholder)", url="#placeholder_cyber_cybrary_threats", resource_type="Resource", step_id=s2_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Cryptography Basics (Khan Academy - Placeholder)", url="#placeholder_cyber_khan_crypto", resource_type="Course", step_id=s2_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Thinking Like an Attacker (Blog)", url="#placeholder_cyber_blog_mindset", resource_type="Article", step_id=s2_4_cy.id))

                    # Milestone 3: Operating Systems Security
                    m3_cy = Milestone(name="Operating Systems Security (Linux/Windows)", sequence=30, career_path_id=cyber_path.id); db.session.add(m3_cy); db.session.flush()
                    s3_1_cy = Step(name="Linux Command Line Basics", sequence=10, estimated_time_minutes=300, milestone_id=m3_cy.id)
                    s3_2_cy = Step(name="Linux File Permissions & Users", sequence=20, estimated_time_minutes=180, milestone_id=m3_cy.id)
                    s3_3_cy = Step(name="Windows Security Basics (Users, Permissions, Policies)", sequence=30, estimated_time_minutes=240, milestone_id=m3_cy.id)
                    s3_4_cy = Step(name="OS Hardening Concepts", sequence=40, estimated_time_minutes=120, milestone_id=m3_cy.id)
                    db.session.add_all([s3_1_cy, s3_2_cy, s3_3_cy, s3_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Linux Journey (Website)", url="#placeholder_cyber_linuxjourney", resource_type="Tutorial", step_id=s3_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Linux Permissions Explained (Article)", url="#placeholder_cyber_article_linuxperm", resource_type="Article", step_id=s3_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Windows Security Settings (MS Docs)", url="#placeholder_cyber_msdocs_winsec", resource_type="Documentation", step_id=s3_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="CIS Benchmarks Intro", url="#placeholder_cyber_cis_intro", resource_type="Resource", step_id=s3_4_cy.id))

                    # Milestone 4: Network Security Fundamentals
                    m4_cy = Milestone(name="Network Security Fundamentals", sequence=40, career_path_id=cyber_path.id); db.session.add(m4_cy); db.session.flush()
                    s4_1_cy = Step(name="Firewall Concepts & Rule Basics", sequence=10, estimated_time_minutes=180, milestone_id=m4_cy.id)
                    s4_2_cy = Step(name="VPN Technologies Overview (IPSec, SSL/TLS)", sequence=20, estimated_time_minutes=120, milestone_id=m4_cy.id)
                    s4_3_cy = Step(name="Intrusion Detection/Prevention Systems (IDS/IPS)", sequence=30, estimated_time_minutes=120, milestone_id=m4_cy.id)
                    s4_4_cy = Step(name="Wireless Security (WPA2/WPA3, Best Practices)", sequence=40, estimated_time_minutes=90, milestone_id=m4_cy.id)
                    db.session.add_all([s4_1_cy, s4_2_cy, s4_3_cy, s4_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Firewalls Explained (Video)", url="#placeholder_cyber_yt_firewall", resource_type="Video", step_id=s4_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="How VPNs Work (Article)", url="#placeholder_cyber_article_vpn", resource_type="Article", step_id=s4_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="IDS vs IPS (Article)", url="#placeholder_cyber_article_idsips", resource_type="Article", step_id=s4_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Wi-Fi Security Guide", url="#placeholder_cyber_guide_wifi", resource_type="Guide", step_id=s4_4_cy.id))

                    # Milestone 5: Security Tools & Technologies Intro
                    m5_cy = Milestone(name="Security Tools & Technologies Intro", sequence=50, career_path_id=cyber_path.id); db.session.add(m5_cy); db.session.flush()
                    s5_1_cy = Step(name="Network Scanning with Nmap (Basics)", sequence=10, estimated_time_minutes=240, milestone_id=m5_cy.id)
                    s5_2_cy = Step(name="Packet Analysis with Wireshark (Basics)", sequence=20, estimated_time_minutes=300, milestone_id=m5_cy.id)
                    s5_3_cy = Step(name="Vulnerability Scanner Concepts (e.g., Nessus, OpenVAS)", sequence=30, estimated_time_minutes=120, milestone_id=m5_cy.id)
                    s5_4_cy = Step(name="SIEM Introduction (e.g., Splunk Free, ELK Stack)", sequence=40, estimated_time_minutes=180, milestone_id=m5_cy.id)
                    db.session.add_all([s5_1_cy, s5_2_cy, s5_3_cy, s5_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Nmap Basics (TryHackMe - Placeholder)", url="#placeholder_cyber_thm_nmap", resource_type="Practice", step_id=s5_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Wireshark Tutorial (Video)", url="#placeholder_cyber_yt_wireshark", resource_type="Video", step_id=s5_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Vulnerability Scanning Overview (Article)", url="#placeholder_cyber_article_vulnscan", resource_type="Article", step_id=s5_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="What is SIEM? (Splunk)", url="#placeholder_cyber_splunk_siem", resource_type="Article", step_id=s5_4_cy.id))

                    # Milestone 6: Threats, Vulnerabilities & Risk Management
                    m6_cy = Milestone(name="Threats, Vulnerabilities & Risk Management", sequence=60, career_path_id=cyber_path.id); db.session.add(m6_cy); db.session.flush()
                    s6_1_cy = Step(name="Common Malware Types (Virus, Worm, Trojan, Ransomware)", sequence=10, estimated_time_minutes=120, milestone_id=m6_cy.id)
                    s6_2_cy = Step(name="Social Engineering Tactics (Phishing, Pretexting)", sequence=20, estimated_time_minutes=90, milestone_id=m6_cy.id)
                    s6_3_cy = Step(name="OWASP Top 10 Web Vulnerabilities Awareness", sequence=30, estimated_time_minutes=180, milestone_id=m6_cy.id)
                    s6_4_cy = Step(name="Risk Management Concepts (Assessment, Mitigation)", sequence=40, estimated_time_minutes=120, milestone_id=m6_cy.id)
                    db.session.add_all([s6_1_cy, s6_2_cy, s6_3_cy, s6_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Malware Types Explained (Malwarebytes)", url="#placeholder_cyber_mb_malware", resource_type="Resource", step_id=s6_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Social Engineering Attacks (Article)", url="#placeholder_cyber_article_soceng", resource_type="Article", step_id=s6_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="OWASP Top 10 Project", url="#placeholder_cyber_owasp_top10", resource_type="Resource", step_id=s6_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Intro to IT Risk Management (Video)", url="#placeholder_cyber_yt_risk", resource_type="Video", step_id=s6_4_cy.id))

                    # Milestone 7: Ethical Hacking Concepts
                    m7_cy = Milestone(name="Introduction to Ethical Hacking Concepts", sequence=70, career_path_id=cyber_path.id); db.session.add(m7_cy); db.session.flush()
                    s7_1_cy = Step(name="Phases of Penetration Testing", sequence=10, estimated_time_minutes=90, milestone_id=m7_cy.id)
                    s7_2_cy = Step(name="Reconnaissance Techniques (Passive/Active)", sequence=20, estimated_time_minutes=180, milestone_id=m7_cy.id)
                    s7_3_cy = Step(name="Basic Exploitation Concepts (Metasploit Intro)", sequence=30, estimated_time_minutes=240, milestone_id=m7_cy.id)
                    s7_4_cy = Step(name="Ethical Considerations & Reporting", sequence=40, estimated_time_minutes=60, milestone_id=m7_cy.id)
                    db.session.add_all([s7_1_cy, s7_2_cy, s7_3_cy, s7_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Pentest Phases Explained (Article)", url="#placeholder_cyber_article_pentest", resource_type="Article", step_id=s7_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Google Dorking / OSINT Basics", url="#placeholder_cyber_guide_osint", resource_type="Guide", step_id=s7_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Metasploit Unleashed (OffSec)", url="#placeholder_cyber_offsec_msf", resource_type="Resource", step_id=s7_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Ethical Hacking Ethics (Article)", url="#placeholder_cyber_article_ethics", resource_type="Article", step_id=s7_4_cy.id))

                    # Milestone 8: Security Operations & Incident Response Basics
                    m8_cy = Milestone(name="Security Operations & Incident Response Basics", sequence=80, career_path_id=cyber_path.id); db.session.add(m8_cy); db.session.flush()
                    s8_1_cy = Step(name="Understanding Log Sources & Types", sequence=10, estimated_time_minutes=120, milestone_id=m8_cy.id)
                    s8_2_cy = Step(name="Basic Log Analysis Techniques", sequence=20, estimated_time_minutes=240, milestone_id=m8_cy.id)
                    s8_3_cy = Step(name="Incident Response Lifecycle (e.g., PICERL)", sequence=30, estimated_time_minutes=180, milestone_id=m8_cy.id)
                    s8_4_cy = Step(name="SOC Analyst Role Overview", sequence=40, estimated_time_minutes=60, milestone_id=m8_cy.id)
                    db.session.add_all([s8_1_cy, s8_2_cy, s8_3_cy, s8_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Common Log Sources (Article)", url="#placeholder_cyber_article_logs", resource_type="Article", step_id=s8_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Log Analysis Tutorial (Video)", url="#placeholder_cyber_yt_loganalysis", resource_type="Video", step_id=s8_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Incident Response Steps (SANS)", url="#placeholder_cyber_sans_ir", resource_type="Guide", step_id=s8_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Day in the Life of a SOC Analyst (YT)", url="#placeholder_cyber_yt_soc", resource_type="Video", step_id=s8_4_cy.id))

                    # Milestone 9: Compliance & Frameworks Awareness
                    m9_cy = Milestone(name="Compliance & Frameworks Awareness", sequence=90, career_path_id=cyber_path.id); db.session.add(m9_cy); db.session.flush()
                    s9_1_cy = Step(name="Introduction to NIST Cybersecurity Framework (CSF)", sequence=10, estimated_time_minutes=120, milestone_id=m9_cy.id)
                    s9_2_cy = Step(name="Overview of ISO 27001/27002", sequence=20, estimated_time_minutes=90, milestone_id=m9_cy.id)
                    s9_3_cy = Step(name="Data Privacy Awareness (GDPR/NDPR Basics)", sequence=30, estimated_time_minutes=90, milestone_id=m9_cy.id)
                    db.session.add_all([s9_1_cy, s9_2_cy, s9_3_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="NIST CSF Overview (NIST)", url="#placeholder_cyber_nist_csf", resource_type="Resource", step_id=s9_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="ISO 27001 Explained (Article)", url="#placeholder_cyber_article_iso", resource_type="Article", step_id=s9_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="NDPR Overview (NITDA - Placeholder)", url="#placeholder_cyber_nitda_ndpr", resource_type="Resource", step_id=s9_3_cy.id))

                    # Milestone 10: Career Prep & Certifications (Security+)
                    m10_cy = Milestone(name="Career Prep & Certifications (Security+ Focus)", sequence=100, career_path_id=cyber_path.id); db.session.add(m10_cy); db.session.flush()
                    s10_1_cy = Step(name="Review CompTIA Security+ Exam Objectives", sequence=10, estimated_time_minutes=120, milestone_id=m10_cy.id)
                    s10_2_cy = Step(name="Study Key Security+ Domains (Using Free Resources)", sequence=20, estimated_time_minutes=2400, milestone_id=m10_cy.id) # Very rough large estimate
                    s10_3_cy = Step(name="Cybersecurity Resume & Portfolio Tips", sequence=30, estimated_time_minutes=180, milestone_id=m10_cy.id)
                    s10_4_cy = Step(name="Practice Security Interview Questions", sequence=40, estimated_time_minutes=300, milestone_id=m10_cy.id)
                    db.session.add_all([s10_1_cy, s10_2_cy, s10_3_cy, s10_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Security+ SY0-701 Objectives (CompTIA)", url="#placeholder_cyber_comptia_secplus", resource_type="Guide", step_id=s10_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Professor Messer Security+ Training (YT)", url="#placeholder_cyber_yt_messer", resource_type="Course", step_id=s10_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Cybersecurity Resume Guide (Blog)", url="#placeholder_cyber_blog_resume", resource_type="Article", step_id=s10_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Security Analyst Interview Qs (Article)", url="#placeholder_cyber_article_interview", resource_type="Article", step_id=s10_4_cy.id))

                    # --- Add all collected resources for Cyber ---
                    if resources_to_add_cyber:
                        db.session.add_all(resources_to_add_cyber)

                    db.session.commit()
                    print(f"Path '{cyber_path.name}' seeded successfully.")

                except Exception as e:
                    db.session.rollback()
                    print(f"Error seeding path '{cyber_path.name}': {e}")

            elif cyber_path:
                 print(f"Path '{cyber_path.name}' milestones already seem to exist. Skipping seeding.")
            else:
                 print("Cybersecurity career path not found in DB, skipping seeding.")
            # --- <<< END CYBERSECURITY SEEDING BLOCK >>> ---



            # --- <<< ADD SOFTWARE ENGINEERING SEEDING BLOCK >>> ---
            print("Checking for Software Engineering path seeding...")
            swe_path = CareerPath.query.filter_by(name="Software Engineering").first()

            if swe_path and not Milestone.query.filter_by(career_path_id=swe_path.id).first():
                print(f"Seeding path for '{swe_path.name}'...")
                resources_to_add_swe = []
                try: # Add inner try/except for seeding this specific path
                    # Milestone 1: Programming Fundamentals (Python)
                    m1_swe = Milestone(name="Programming Fundamentals (Python)", sequence=10, career_path_id=swe_path.id); db.session.add(m1_swe); db.session.flush()
                    s1_1_swe = Step(name="Python Setup & Basic Syntax (Variables, Types, Operators)", sequence=10, estimated_time_minutes=180, milestone_id=m1_swe.id)
                    s1_2_swe = Step(name="Control Flow (If/Else, For/While Loops)", sequence=20, estimated_time_minutes=240, milestone_id=m1_swe.id)
                    s1_3_swe = Step(name="Functions & Modules", sequence=30, estimated_time_minutes=300, milestone_id=m1_swe.id)
                    s1_4_swe = Step(name="Basic Object-Oriented Programming (Classes, Objects)", sequence=40, estimated_time_minutes=300, milestone_id=m1_swe.id)
                    s1_5_swe = Step(name="Error Handling (Try/Except)", sequence=50, estimated_time_minutes=120, milestone_id=m1_swe.id)
                    db.session.add_all([s1_1_swe, s1_2_swe, s1_3_swe, s1_4_swe, s1_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Official Python Tutorial", url="#placeholder_swe_python_docs", resource_type="Documentation", step_id=s1_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Python Control Flow (Real Python)", url="#placeholder_swe_rp_control", resource_type="Article", step_id=s1_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Python Functions Guide (Video)", url="#placeholder_swe_yt_functions", resource_type="Video", step_id=s1_3_swe.id))
                    resources_to_add_swe.append(Resource(name="Python OOP Basics (Article)", url="#placeholder_swe_article_oop", resource_type="Article", step_id=s1_4_swe.id))
                    resources_to_add_swe.append(Resource(name="Python Exceptions Handling", url="#placeholder_swe_docs_exceptions", resource_type="Documentation", step_id=s1_5_swe.id))

                    # Milestone 2: Data Structures & Algorithms Basics
                    m2_swe = Milestone(name="Data Structures & Algorithms Basics", sequence=20, career_path_id=swe_path.id); db.session.add(m2_swe); db.session.flush()
                    s2_1_swe = Step(name="Common Data Structures (List, Dict, Set, Tuple)", sequence=10, estimated_time_minutes=180, milestone_id=m2_swe.id)
                    s2_2_swe = Step(name="Understanding Time & Space Complexity (Big O)", sequence=20, estimated_time_minutes=180, milestone_id=m2_swe.id)
                    s2_3_swe = Step(name="Basic Algorithms: Linear/Binary Search", sequence=30, estimated_time_minutes=120, milestone_id=m2_swe.id)
                    s2_4_swe = Step(name="Basic Algorithms: Bubble/Selection/Insertion Sort", sequence=40, estimated_time_minutes=180, milestone_id=m2_swe.id)
                    db.session.add_all([s2_1_swe, s2_2_swe, s2_3_swe, s2_4_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Python Data Structures In-Depth (Article)", url="#placeholder_swe_article_ds", resource_type="Article", step_id=s2_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Big O Notation Explained (Video)", url="#placeholder_swe_yt_bigo", resource_type="Video", step_id=s2_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Searching Algorithms (GeeksForGeeks)", url="#placeholder_swe_gfg_search", resource_type="Article", step_id=s2_3_swe.id))
                    resources_to_add_swe.append(Resource(name="Sorting Algorithms Visualized", url="#placeholder_swe_web_sortviz", resource_type="Resource", step_id=s2_4_swe.id))

                    # Milestone 3: Version Control (Git)
                    m3_swe = Milestone(name="Version Control with Git & GitHub", sequence=30, career_path_id=swe_path.id); db.session.add(m3_swe); db.session.flush()
                    s3_1_swe = Step(name="Install & Configure Git", sequence=10, estimated_time_minutes=60, milestone_id=m3_swe.id)
                    s3_2_swe = Step(name="Core Git Commands (init, add, commit, status, log)", sequence=20, estimated_time_minutes=180, milestone_id=m3_swe.id)
                    s3_3_swe = Step(name="Branching & Merging Basics", sequence=30, estimated_time_minutes=180, milestone_id=m3_swe.id)
                    s3_4_swe = Step(name="Working with Remote Repositories (GitHub/GitLab - clone, push, pull, fetch)", sequence=40, estimated_time_minutes=240, milestone_id=m3_swe.id)
                    s3_5_swe = Step(name="Understanding Pull Requests / Merge Requests", sequence=50, estimated_time_minutes=60, milestone_id=m3_swe.id)
                    db.session.add_all([s3_1_swe, s3_2_swe, s3_3_swe, s3_4_swe, s3_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Pro Git Book (Free Online)", url="#placeholder_swe_gitbook", resource_type="Book", step_id=s3_1_swe.id)) # Covers all steps
                    resources_to_add_swe.append(Resource(name="Git Tutorial (Atlassian)", url="#placeholder_swe_atlassian_git", resource_type="Tutorial", step_id=s3_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Git Branching Model (Article)", url="#placeholder_swe_article_gitbranch", resource_type="Article", step_id=s3_3_swe.id))
                    resources_to_add_swe.append(Resource(name="GitHub Docs", url="#placeholder_swe_github_docs", resource_type="Documentation", step_id=s3_4_swe.id))
                    resources_to_add_swe.append(Resource(name="Understanding Pull Requests (GitHub)", url="#placeholder_swe_github_pr", resource_type="Guide", step_id=s3_5_swe.id))

                    # Milestone 4: Web Concepts
                    m4_swe = Milestone(name="Introduction to Web Concepts", sequence=40, career_path_id=swe_path.id); db.session.add(m4_swe); db.session.flush()
                    s4_1_swe = Step(name="How the Web Works (Client/Server, HTTP/HTTPS)", sequence=10, estimated_time_minutes=120, milestone_id=m4_swe.id)
                    s4_2_swe = Step(name="HTML Basics (Structure, Tags)", sequence=20, estimated_time_minutes=240, milestone_id=m4_swe.id)
                    s4_3_swe = Step(name="CSS Basics (Selectors, Box Model, Layout)", sequence=30, estimated_time_minutes=300, milestone_id=m4_swe.id)
                    s4_4_swe = Step(name="JavaScript Basics (DOM Manipulation, Events)", sequence=40, estimated_time_minutes=480, milestone_id=m4_swe.id)
                    db.session.add_all([s4_1_swe, s4_2_swe, s4_3_swe, s4_4_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="How the Internet Works (Video)", url="#placeholder_swe_yt_webworks", resource_type="Video", step_id=s4_1_swe.id))
                    resources_to_add_swe.append(Resource(name="HTML Tutorial (MDN)", url="#placeholder_swe_mdn_html", resource_type="Tutorial", step_id=s4_2_swe.id))
                    resources_to_add_swe.append(Resource(name="CSS Tutorial (MDN)", url="#placeholder_swe_mdn_css", resource_type="Tutorial", step_id=s4_3_swe.id))
                    resources_to_add_swe.append(Resource(name="JavaScript Tutorial (MDN)", url="#placeholder_swe_mdn_js", resource_type="Tutorial", step_id=s4_4_swe.id))

                    # Milestone 5: Backend Development Framework (Flask)
                    m5_swe = Milestone(name="Backend Development Framework (Flask)", sequence=50, career_path_id=swe_path.id); db.session.add(m5_swe); db.session.flush()
                    s5_1_swe = Step(name="Flask Setup & Basic Routing", sequence=10, estimated_time_minutes=180, milestone_id=m5_swe.id)
                    s5_2_swe = Step(name="Using Templates (Jinja2)", sequence=20, estimated_time_minutes=180, milestone_id=m5_swe.id)
                    s5_3_swe = Step(name="Handling Forms (Flask-WTF)", sequence=30, estimated_time_minutes=240, milestone_id=m5_swe.id)
                    s5_4_swe = Step(name="Request Object & Response Cycle", sequence=40, estimated_time_minutes=120, milestone_id=m5_swe.id)
                    s5_5_swe = Step(name="Introduction to Blueprints", sequence=50, estimated_time_minutes=120, milestone_id=m5_swe.id)
                    db.session.add_all([s5_1_swe, s5_2_swe, s5_3_swe, s5_4_swe, s5_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Flask Quickstart (Official Docs)", url="#placeholder_swe_flask_quickstart", resource_type="Documentation", step_id=s5_1_swe.id)) # Covers several steps
                    resources_to_add_swe.append(Resource(name="Flask Templates Guide", url="#placeholder_swe_flask_templates", resource_type="Documentation", step_id=s5_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask-WTF Forms Guide", url="#placeholder_swe_flask_forms", resource_type="Documentation", step_id=s5_3_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask Request Object", url="#placeholder_swe_flask_request", resource_type="Documentation", step_id=s5_4_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask Blueprints", url="#placeholder_swe_flask_blueprints", resource_type="Documentation", step_id=s5_5_swe.id))

                    # Milestone 6: Databases & ORMs
                    m6_swe = Milestone(name="Databases & ORMs", sequence=60, career_path_id=swe_path.id); db.session.add(m6_swe); db.session.flush()
                    s6_1_swe = Step(name="SQL Refresher (Joins, Aggregations)", sequence=10, estimated_time_minutes=180, milestone_id=m6_swe.id)
                    s6_2_swe = Step(name="Using SQLAlchemy with Flask (Models, Sessions)", sequence=20, estimated_time_minutes=300, milestone_id=m6_swe.id)
                    s6_3_swe = Step(name="Database Migrations with Flask-Migrate (Alembic)", sequence=30, estimated_time_minutes=180, milestone_id=m6_swe.id)
                    db.session.add_all([s6_1_swe, s6_2_swe, s6_3_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="SQLBolt (Interactive SQL Tutorial)", url="#placeholder_swe_sqlbolt", resource_type="Tutorial", step_id=s6_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask-SQLAlchemy Docs", url="#placeholder_swe_flask_sqlalchemy", resource_type="Documentation", step_id=s6_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask-Migrate Docs", url="#placeholder_swe_flask_migrate", resource_type="Documentation", step_id=s6_3_swe.id))

                    # Milestone 7: APIs (REST)
                    m7_swe = Milestone(name="APIs (RESTful Principles)", sequence=70, career_path_id=swe_path.id); db.session.add(m7_swe); db.session.flush()
                    s7_1_swe = Step(name="Understanding REST Concepts (Stateless, Resources, Methods)", sequence=10, estimated_time_minutes=120, milestone_id=m7_swe.id)
                    s7_2_swe = Step(name="Building a Simple JSON API with Flask", sequence=20, estimated_time_minutes=300, milestone_id=m7_swe.id)
                    s7_3_swe = Step(name="Consuming APIs with Python `requests` library", sequence=30, estimated_time_minutes=120, milestone_id=m7_swe.id)
                    db.session.add_all([s7_1_swe, s7_2_swe, s7_3_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="What is REST? (Article)", url="#placeholder_swe_article_rest", resource_type="Article", step_id=s7_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Build Flask API (Tutorial)", url="#placeholder_swe_tut_flaskapi", resource_type="Tutorial", step_id=s7_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Python Requests Library Docs", url="#placeholder_swe_requests_docs", resource_type="Documentation", step_id=s7_3_swe.id))

                    # Milestone 8: Testing Fundamentals
                    m8_swe = Milestone(name="Testing Fundamentals", sequence=80, career_path_id=swe_path.id); db.session.add(m8_swe); db.session.flush()
                    s8_1_swe = Step(name="Why Test? (Benefits, Types of Tests)", sequence=10, estimated_time_minutes=60, milestone_id=m8_swe.id)
                    s8_2_swe = Step(name="Unit Testing Basics with `pytest`", sequence=20, estimated_time_minutes=180, milestone_id=m8_swe.id)
                    s8_3_swe = Step(name="Testing Flask Applications (Test Client, Fixtures)", sequence=30, estimated_time_minutes=240, milestone_id=m8_swe.id)
                    db.session.add_all([s8_1_swe, s8_2_swe, s8_3_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Why Write Tests? (Blog)", url="#placeholder_swe_blog_whytest", resource_type="Article", step_id=s8_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Pytest Introduction (Docs)", url="#placeholder_swe_pytest_docs", resource_type="Documentation", step_id=s8_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Testing Flask Apps (Flask Docs)", url="#placeholder_swe_flask_testing", resource_type="Documentation", step_id=s8_3_swe.id))

                    # Milestone 9: Deployment Concepts
                    m9_swe = Milestone(name="Deployment Concepts", sequence=90, career_path_id=swe_path.id); db.session.add(m9_swe); db.session.flush()
                    s9_1_swe = Step(name="Web Servers (Gunicorn) & WSGI", sequence=10, estimated_time_minutes=120, milestone_id=m9_swe.id)
                    s9_2_swe = Step(name="Introduction to Docker (Dockerfile, Images, Containers)", sequence=20, estimated_time_minutes=300, milestone_id=m9_swe.id)
                    s9_3_swe = Step(name="Cloud Hosting Options Overview (PaaS, IaaS - e.g., Railway, Heroku, AWS EC2)", sequence=30, estimated_time_minutes=120, milestone_id=m9_swe.id)
                    s9_4_swe = Step(name="Environment Variables for Configuration", sequence=40, estimated_time_minutes=60, milestone_id=m9_swe.id)
                    db.session.add_all([s9_1_swe, s9_2_swe, s9_3_swe, s9_4_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Gunicorn Docs", url="#placeholder_swe_gunicorn_docs", resource_type="Documentation", step_id=s9_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Docker Get Started Guide", url="#placeholder_swe_docker_docs", resource_type="Documentation", step_id=s9_2_swe.id))
                    resources_to_add_swe.append(Resource(name="PaaS vs IaaS (Article)", url="#placeholder_swe_article_paas", resource_type="Article", step_id=s9_3_swe.id))
                    resources_to_add_swe.append(Resource(name="Using Environment Variables (Blog)", url="#placeholder_swe_blog_envvars", resource_type="Article", step_id=s9_4_swe.id))

                    # Milestone 10: Building & Showcasing Projects
                    m10_swe = Milestone(name="Building & Showcasing Projects", sequence=100, career_path_id=swe_path.id); db.session.add(m10_swe); db.session.flush()
                    s10_1_swe = Step(name="Plan and Scope a Small Full-Stack Project", sequence=10, estimated_time_minutes=180, milestone_id=m10_swe.id)
                    s10_2_swe = Step(name="Build Project using Flask, SQLAlchemy, etc.", sequence=20, estimated_time_minutes=1800, milestone_id=m10_swe.id) # Significant time
                    s10_3_swe = Step(name="Write Basic Tests for Your Project", sequence=30, estimated_time_minutes=300, milestone_id=m10_swe.id)
                    s10_4_swe = Step(name="Document Your Project (README on GitHub)", sequence=40, estimated_time_minutes=180, milestone_id=m10_swe.id)
                    s10_5_swe = Step(name="Deploy Your Project (e.g., on Railway)", sequence=50, estimated_time_minutes=240, milestone_id=m10_swe.id)
                    db.session.add_all([s10_1_swe, s10_2_swe, s10_3_swe, s10_4_swe, s10_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Flask Project Ideas (Blog)", url="#placeholder_swe_blog_ideas", resource_type="Article", step_id=s10_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Example Flask Projects (GitHub Search)", url="#placeholder_swe_github_search", resource_type="Resource", step_id=s10_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Writing Good READMEs", url="#placeholder_swe_article_readme", resource_type="Article", step_id=s10_4_swe.id))
                    resources_to_add_swe.append(Resource(name="Deploying Flask to Railway (Docs)", url="#placeholder_swe_railway_flask", resource_type="Documentation", step_id=s10_5_swe.id))


                    # --- Add all collected resources for SWE ---
                    if resources_to_add_swe:
                        db.session.add_all(resources_to_add_swe)

                    db.session.commit()
                    print(f"Path '{swe_path.name}' seeded successfully.")

                except Exception as e:
                    db.session.rollback()
                    print(f"Error seeding path '{swe_path.name}': {e}")

            elif swe_path:
                 print(f"Path '{swe_path.name}' milestones already seem to exist. Skipping seeding.")
            else:
                 print("Software Engineering career path not found in DB, skipping seeding.")
            # --- <<< END SOFTWARE ENGINEERING SEEDING BLOCK >>> ---


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
    app.run(debug=True, host='0.0.0.0', port=port)
