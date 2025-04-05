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
    # --- << NEW: Check for association ID on GET >> ---
    step_id_from_url = request.args.get('step_id', type=int)
    milestone_id_from_url = request.args.get('milestone_id', type=int) # Allow for future use
    # You might want to fetch the Step/Milestone object here to display its name
    linked_item_name = None
    if step_id_from_url:
        linked_step = Step.query.get(step_id_from_url)
        if linked_step:
            linked_item_name = f"Step: {linked_step.name}"
    # Add similar logic for milestone_id if needed
    # --- << End Check >> ---
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

        # --- << NEW: Get association IDs from hidden form fields (if added) >> ---
        # Or simply use the variables captured during the GET request if not editing
        # For simplicity, let's reuse the IDs captured on GET for the initial Add.
        # If editing, we'd handle association differently.
        assoc_step_id = step_id_from_url # Use ID captured when page loaded
        assoc_milestone_id = milestone_id_from_url # Use ID captured when page loaded
        # --- << End Get Association >> ---

        new_item = PortfolioItem(
            user_id=current_user.id,
            title=form.title.data,
            description=form.description.data,
            item_type=form.item_type.data,
            link_url=link_url if link_url else None,
            file_filename=file_filename_to_save,
            # --- << NEW: Set association IDs >> ---
            associated_step_id=assoc_step_id,
            associated_milestone_id=assoc_milestone_id
            # --- << End Set Association >> ---
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

    return render_template('add_edit_portfolio_item.html', 
                           title='Add Portfolio Item', 
                           form=form, 
                           is_edit=False, 
                           step_id=step_id_from_url, # Pass ID
                           milestone_id=milestone_id_from_url, # Pass ID
                           linked_item_name=linked_item_name, # Pass name for display
                           is_homepage=False)


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
