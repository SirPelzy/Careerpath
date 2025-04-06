import os
import uuid
import datetime
from flask_migrate import Migrate
from werkzeug.utils import secure_filename # To sanitize filenames
from flask import Flask, render_template, redirect, url_for, flash, request, abort, current_app, session
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from dotenv import load_dotenv
from sqlalchemy.orm import selectinload
from models import db, User, CareerPath, Milestone, Step, Resource, UserStepStatus, PortfolioItem
from forms import RegistrationForm, LoginForm, OnboardingForm, PortfolioItemForm, EditProfileForm, RecommendationTestForm
from forms import RequestResetForm, ResetPasswordForm
from itsdangerous import URLSafeTimedSerializer as Serializer

print("DEBUG: Importing Migrate...") # <-- Add Print
try:
    from flask_migrate import Migrate
    print("DEBUG: Imported Migrate successfully.") # <-- Add Print
except ImportError as e:
    print(f"DEBUG: FAILED to import Migrate: {e}")
    Migrate = None
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

migrate = Migrate(app, db) # Initialize Flask-Migrate

# Initialize Migrate only if import succeeded
migrate = None
if Migrate:
    print("DEBUG: Attempting to initialize Migrate...") # <-- Add Print
    try:
        migrate = Migrate(app, db)
        print("DEBUG: Initialized Migrate successfully.") # <-- Add Print
    except Exception as e:
         print(f"DEBUG: ERROR initializing Migrate: {e}") # <-- Add Print
         migrate = None # Prevent further errors if init fails
else:
    print("DEBUG: Skipping Migrate initialization due to import failure.") # <-- Add Print

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
        # --- MODIFIED Query: Eagerly load steps ---
        milestones = Milestone.query.options(
            selectinload(Milestone.steps) # Eager load the 'steps' for each milestone
        ).filter_by(
            career_path_id=target_path.id
        ).order_by(Milestone.sequence).all()
        # --- End MODIFIED Query ---
        
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

            try:
                s = Serializer(current_app.config['SECRET_KEY'])
                token_salt = 'email-confirm-salt' # Different salt
                token = s.dumps(user.id, salt=token_salt)
                verify_url = url_for('verify_token', token=token, _external=True)

                # Store in session for DEV MODE modal
                session['show_verify_modal'] = True
                session['verify_url'] = verify_url
                print(f"DEBUG: Stored verify URL in session for {user.email}")

                # Flash message indicating verification needed (and showing link for dev)
                flash('Your account has been created! Please check the pop-up on the login page to verify your email (DEV MODE).', 'success')

                # --- PRODUCTION Email code would go here ---
                # msg = Message(...) send email with verify_url ...
                # --- End Production ---
            except Exception as e_token:
                 # Log error if token generation fails, but let user log in
                 print(f"Error generating verification token for {user.email}: {e_token}")
                 flash('Your account was created, but verification link generation failed. Please contact support or try resetting password later.', 'warning')
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
    # --- Check for reset modal info from session on GET request ---
    show_modal = session.pop('show_reset_modal', False) # Get value and remove from session
    reset_url = session.pop('reset_url', None) # Get value and remove from session
    # --- End Check ---
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
    return render_template('login.html', title='Login', form=form, is_homepage=True, show_reset_modal=show_modal, reset_url=reset_url)

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
    form = RecommendationTestForm()
    if form.validate_on_submit():
        # --- Basic Rule-Based Scoring ---
        # Initialize scores for all potential paths
        scores = {
            "Data Analysis / Analytics": 0,
            "UX/UI Design": 0,
            "Software Engineering": 0,
            "Cybersecurity": 0
        }
        answers = {
            'q1': form.q1_hobby.data,
            'q2': form.q2_approach.data,
            'q3': form.q3_reward.data,
            'q4': form.q4_feedback.data
        }

        # Apply scoring based on answers (Adjust scores/logic as needed)
        # Q1: Hobby Preference
        if answers['q1'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q1'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q1'] == 'C': scores["Software Engineering"] += 1
        elif answers['q1'] == 'D': scores["Cybersecurity"] += 1

        # Q2: Problem Approach
        if answers['q2'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q2'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q2'] == 'C': scores["Software Engineering"] += 1
        elif answers['q2'] == 'D': scores["Cybersecurity"] += 1

        # Q3: Rewarding Activity
        if answers['q3'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q3'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q3'] == 'C': scores["Software Engineering"] += 1
        elif answers['q3'] == 'D': scores["Cybersecurity"] += 1

        # Q4: Feedback Focus
        if answers['q4'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q4'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q4'] == 'C': scores["Software Engineering"] += 1
        elif answers['q4'] == 'D': scores["Cybersecurity"] += 1

        # Determine highest score among *available* paths
        # Make sure these names exactly match the names seeded in the CareerPath table
        available_paths = {"Data Analysis / Analytics", "UX/UI Design", "Cybersecurity", "Software Engineering"}
        filtered_scores = {path: score for path, score in scores.items() if path in available_paths and score > 0}

        recommended_path_name = None
        if not filtered_scores:
             # Default recommendation if no path scored > 0
             recommended_path_name = "Data Analysis / Analytics"
             flash("Your answers didn't strongly match a specific path, suggesting Data Analysis as a starting point.", "info")
        else:
             # Find the path name with the highest score
             recommended_path_name = max(filtered_scores, key=filtered_scores.get)
             # Optional: Handle ties here if desired (e.g., recommend multiple or use a tie-breaker rule)
             max_score = filtered_scores[recommended_path_name]
             tied_paths = [path for path, score in filtered_scores.items() if score == max_score]
             if len(tied_paths) > 1:
                  flash(f"You showed strong interest in multiple areas ({', '.join(tied_paths)})! We're recommending {recommended_path_name} to start.", "info")


        # Find the corresponding CareerPath object ID in the database
        recommended_path = CareerPath.query.filter_by(name=recommended_path_name).first()

        if recommended_path:
            # Redirect to results page, passing recommendation info
            return redirect(url_for('recommendation_results',
                                    recommended_path_id=recommended_path.id,
                                    recommended_path_name=recommended_path.name))
        else:
            # This should only happen if the recommended path name isn't in the DB (seeding error)
            flash(f'Could not process recommendation (path "{recommended_path_name}" not found in database). Please select a path manually.', 'danger')
            return redirect(url_for('onboarding_form'))

    # Render the test form on GET request or if POST validation fails
    return render_template('recommendation_test.html',
                           title="Career Recommendation Test",
                           form=form,
                           is_homepage=False) # Use sidebar layout

# --- Recommendation Results Route ---
@app.route('/recommendation-results') # GET only is appropriate here
@login_required
def recommendation_results():
    """Displays the recommendation results and next steps."""
    # Retrieve the recommended path details passed via query parameters
    recommended_path_id = request.args.get('recommended_path_id', type=int)
    recommended_path_name = request.args.get('recommended_path_name')

    # Basic check if the necessary parameters were passed
    if not recommended_path_id or not recommended_path_name:
        flash('Recommendation results not found or invalid. Please try the test again.', 'warning')
        # Redirect back to the test page if data is missing
        return redirect(url_for('recommendation_test'))

    # Render the results template, passing the recommendation details
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
    # Find the step or return 404 Not Found
    step = Step.query.get_or_404(step_id)

    # Optional: Check if the step actually belongs to the user's current path
    # This adds complexity but prevents users potentially toggling steps from other paths
    # if not step.milestone or step.milestone.career_path_id != current_user.target_career_path_id:
    #     flash("Cannot modify status for a step not in your current path.", "warning")
    #     return redirect(url_for('dashboard'))

    # Find existing status record for this user and step, or None
    user_status = UserStepStatus.query.filter_by(user_id=current_user.id, step_id=step.id).first()

    try:
        if user_status:
            # If a status record exists, toggle it
            if user_status.status == 'completed':
                user_status.status = 'not_started'
                user_status.completed_at = None # Clear completion time
                flash(f'Step "{step.name}" marked as not started.', 'info')
            else:
                user_status.status = 'completed'
                user_status.completed_at = datetime.datetime.utcnow() # Set completion time
                flash(f'Step "{step.name}" marked as completed!', 'success')
        else:
            # If no status record exists, create a new one and mark it completed
            user_status = UserStepStatus(
                user_id=current_user.id,
                step_id=step.id,
                status='completed',
                completed_at=datetime.datetime.utcnow()
            )
            db.session.add(user_status)
            flash(f'Step "{step.name}" marked as completed!', 'success')

        # Commit the change (either update or add)
        db.session.commit()

    except Exception as e:
        # Rollback in case of database error
        db.session.rollback()
        print(f"Error updating step status for user {current_user.id}, step {step_id}: {e}")
        flash('An error occurred while updating step status.', 'danger')

    # Redirect back to the dashboard after processing
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

# --- Portfolio Add Route ---
@app.route('/portfolio/add', methods=['GET', 'POST'])
@login_required
def add_portfolio_item():
    """Handles adding a new portfolio item, optionally linked to a step/milestone."""
    form = PortfolioItemForm()

    # --- Get association info only on GET to display linked item name ---
    step_id_from_url = None
    milestone_id_from_url = None # Allow for future use
    linked_item_name = None
    if request.method == 'GET':
        step_id_from_url = request.args.get('step_id', type=int)
        milestone_id_from_url = request.args.get('milestone_id', type=int)
        if step_id_from_url:
            # Query the step to display its name - handle if not found
            linked_step = Step.query.get(step_id_from_url)
            if linked_step:
                linked_item_name = f"Step: {linked_step.name}"
            else:
                step_id_from_url = None # Clear invalid ID
                flash("Invalid associated step ID provided.", "warning")
        # Add similar logic for milestone_id if needed

    if form.validate_on_submit():
        # Executed on valid POST request
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
                # Ensure get_portfolio_upload_path helper exists or define path directly
                file_path = get_portfolio_upload_path(file_filename_to_save)
                file.save(file_path)
                print(f"Portfolio file saved to: {file_path}")
            except Exception as e:
                print(f"Error saving portfolio file: {e}")
                flash('Error uploading file. Please try again.', 'danger')
                file_filename_to_save = None # Don't save filename in DB if file save failed

        # --- Read association IDs from hidden fields in the submitted FORM data ---
        assoc_step_id = request.form.get('associated_step_id', type=int)
        assoc_milestone_id = request.form.get('associated_milestone_id', type=int)
        # --- End Read Association ---

        # Create new PortfolioItem object
        new_item = PortfolioItem(
            user_id=current_user.id,
            title=form.title.data,
            description=form.description.data,
            item_type=form.item_type.data,
            link_url=link_url if link_url else None,
            file_filename=file_filename_to_save,
            # Set association IDs read from the form
            associated_step_id=assoc_step_id,
            associated_milestone_id=assoc_milestone_id
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
            # Re-render form if save fails, passing original IDs back
            return render_template('add_edit_portfolio_item.html',
                                   title='Add Portfolio Item',
                                   form=form,
                                   is_edit=False,
                                   step_id=assoc_step_id, # Use ID from form attempt
                                   milestone_id=assoc_milestone_id, # Use ID from form attempt
                                   linked_item_name=linked_item_name, # Keep name from original GET
                                   is_homepage=False)


    # Render template for GET request or if POST validation failed
    # Pass association IDs from URL (if any) to pre-populate hidden fields on initial GET
    return render_template('add_edit_portfolio_item.html',
                           title='Add Portfolio Item',
                           form=form,
                           is_edit=False,
                           step_id=step_id_from_url, # ID from URL for hidden field
                           milestone_id=milestone_id_from_url, # ID from URL for hidden field
                           linked_item_name=linked_item_name, # Name from URL for display
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

# --- Password Reset Routes ---

@app.route("/reset_password", methods=['GET', 'POST'])
def request_reset():
    """Route for requesting a password reset."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        # --- MODIFIED Logic ---
        reset_url_for_dev = None # Variable to potentially store URL for session
        if user:
            # Generate Token
            s = Serializer(current_app.config['SECRET_KEY'])
            token_salt = 'password-reset-salt'
            token = s.dumps(user.id, salt=token_salt)
            reset_url = url_for('reset_token', token=token, _external=True) # Generate full URL

            # Store URL in session for DEV MODE pop-up
            reset_url_for_dev = reset_url
            session['show_reset_modal'] = True
            session['reset_url'] = reset_url_for_dev
            print(f"DEBUG: Stored reset URL in session for {user.email}") # Optional debug print

            # --- !!! PRODUCTION: Send Email (Requires Flask-Mail setup) !!! ---
            # msg = Message('Password Reset Request',
            #               sender='noreply@yourdomain.com',
            #               recipients=[user.email])
            # msg.body = f'''To reset your password, visit the following link:
            # {reset_url}

            # If you did not make this request then simply ignore this email and no changes will be made.
            # This link will expire in 30 minutes.
            # '''
            # mail.send(msg) # Assumes 'mail = Mail(app)' is configured
            # flash('An email has been sent with instructions to reset your password.', 'info')
            # --- !!! END PRODUCTION --- !!!

        else:
             # Still flash generic message even if user not found for security
             flash('If an account exists for that email, instructions to reset your password have been sent.', 'info')

        return redirect(url_for('login')) # Redirect to login after request

    # Determine layout based on authentication status
    is_homepage_layout = not current_user.is_authenticated
    return render_template('request_reset.html', title='Reset Password Request', form=form, is_homepage=is_homepage_layout)


@app.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_token(token):
    """Route for resetting password using a token."""
    if current_user.is_authenticated:
        return redirect(url_for('home')) # Or dashboard

    # Use the static method on User model to verify token
    user = User.verify_reset_token(token)

    if user is None:
        flash('That is an invalid or expired token. Please request a new one.', 'warning')
        return redirect(url_for('request_reset'))

    # If token is valid, show the password reset form
    form = ResetPasswordForm()
    if form.validate_on_submit():
        try:
            user.set_password(form.password.data) # Use the existing method to hash password
            db.session.commit()
            flash('Your password has been updated! You are now able to log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            print(f"Error resetting password for user {user.id}: {e}")
            flash('An error occurred while resetting your password. Please try again.', 'danger')

    # Determine layout based on authentication status
    is_homepage_layout = not current_user.is_authenticated
    return render_template('reset_password.html', title='Reset Password', form=form, token=token, is_homepage=is_homepage_layout)



# --- <<< TEMPORARY ADMIN ROUTE FOR DB INIT & SEEDING >>> ---
# !! IMPORTANT !! Remove or secure this route in production after successful seeding!
INIT_DB_SECRET_KEY = os.environ.get('INIT_DB_SECRET_KEY', 'replace-this-with-a-very-secret-key-9876')

@app.route(f'/admin/init-db/{INIT_DB_SECRET_KEY}')
def init_database():
    """Temporary route to initialize the database (create missing tables) and seed path data."""
    print("Attempting database initialization and seeding...")
    try:
        with app.app_context():
            # Create all tables that don't exist
            db.create_all()
            print("Database tables checked/created.")

            # --- Seed Initial Career Paths (if needed) ---
            if not CareerPath.query.first():
                print("Populating initial Career Paths...")
                paths = [
                    CareerPath(name="Data Analysis / Analytics", description="Focuses on interpreting data, finding insights, and visualization."),
                    CareerPath(name="UX/UI Design", description="Focuses on user experience and interface design for digital products."),
                    CareerPath(name="Cybersecurity", description="Focuses on protecting computer systems and networks from threats."),
                    CareerPath(name="Software Engineering", description="Focuses on designing, developing, and maintaining software systems.")
                ]
                db.session.add_all(paths)
                db.session.commit() # Commit paths before seeding depends on them
                print("Career Paths added.")
            else:
                print("Career Paths already exist.")

            # --- Seed Data Analytics Path ---
            print("Checking for Data Analysis path seeding...")
            da_path = CareerPath.query.filter_by(name="Data Analysis / Analytics").first()

            if da_path and not Milestone.query.filter_by(career_path_id=da_path.id).first():
                print(f"Seeding path for '{da_path.name}'...")
                resources_to_add_da = []
                try:
                    # Milestone 1: Introduction & Foundation
                    m1_da = Milestone(name="Introduction & Foundation", sequence=10, career_path_id=da_path.id); db.session.add(m1_da); db.session.flush()
                    s1_1_da = Step(name="Understand the Data Analyst Role", sequence=10, estimated_time_minutes=60, milestone_id=m1_da.id, step_type='Learning')
                    s1_2_da = Step(name="Find Your Tech Fit", sequence=20, estimated_time_minutes=30, milestone_id=m1_da.id, step_type='Reading')
                    s1_3_da = Step(name="Learn How to Learn Effectively", sequence=30, estimated_time_minutes=30, milestone_id=m1_da.id, step_type='Reading')
                    db.session.add_all([s1_1_da, s1_2_da, s1_3_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="Intro Course (Udemy)", url="https://www.udemy.com/share/106Gg8/", resource_type="Course", step_id=s1_1_da.id))
                    resources_to_add_da.append(Resource(name="Determining Right Tech Career (Medium)", url="https://medium.com/@Sameerah_writes/how-to-determine-the-right-tech-career-for-you-1a7ad90afd75", resource_type="Article", step_id=s1_2_da.id))
                    resources_to_add_da.append(Resource(name="Best Practices for Tech Learning (Medium)", url="https://medium.com/@Sameerah_writes/best-learning-practices-for-tech-courses-in-2023-c9a908f179db", resource_type="Article", step_id=s1_3_da.id))

                    # Milestone 2: Excel for Data Analysis
                    m2_da = Milestone(name="Excel for Data Analysis", sequence=20, career_path_id=da_path.id); db.session.add(m2_da); db.session.flush()
                    s2_1_da = Step(name="Excel Basics Tutorial", sequence=10, estimated_time_minutes=180, milestone_id=m2_da.id, step_type='Learning')
                    s2_2_da = Step(name="Excel Lookup Formulas", sequence=20, estimated_time_minutes=60, milestone_id=m2_da.id, step_type='Learning')
                    s2_3_da = Step(name="Excel Charting Techniques", sequence=30, estimated_time_minutes=60, milestone_id=m2_da.id, step_type='Learning')
                    s2_4_da = Step(name="Data Preparation in Excel", sequence=40, estimated_time_minutes=120, milestone_id=m2_da.id, step_type='Practice')
                    s2_5_da = Step(name="Guided Project: Excel Data Analysis", sequence=50, estimated_time_minutes=120, milestone_id=m2_da.id, step_type='Project')
                    db.session.add_all([s2_1_da, s2_2_da, s2_3_da, s2_4_da, s2_5_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="Excel Basic Tutorial (Alex YT - Placeholder)", url="#placeholder_yt_link_0", resource_type="Video", step_id=s2_1_da.id))
                    resources_to_add_da.append(Resource(name="Excel Lookup Formulas (LeilaG YT - Placeholder)", url="#placeholder_yt_link_2", resource_type="Video", step_id=s2_2_da.id))
                    resources_to_add_da.append(Resource(name="Excel Charts (LeilaG YT - Placeholder)", url="#placeholder_yt_link_4", resource_type="Video", step_id=s2_3_da.id))
                    resources_to_add_da.append(Resource(name="Data Prep Course (DataCamp)", url="https://campus.datacamp.com/courses/data-preparation-in-excel/starting-data-preparation-in-excel?ex=1#", resource_type="Course", step_id=s2_4_da.id))
                    resources_to_add_da.append(Resource(name="Excel Guided Project (Coursera - Placeholder)", url="#placeholder_coursera_link_5", resource_type="Project", step_id=s2_5_da.id))

                    # Milestone 3: SQL Fundamentals
                    m3_da = Milestone(name="SQL Fundamentals", sequence=30, career_path_id=da_path.id); db.session.add(m3_da); db.session.flush()
                    s3_1_da = Step(name="SQL Basics Tutorial", sequence=10, estimated_time_minutes=240, milestone_id=m3_da.id, step_type='Learning')
                    s3_2_da = Step(name="Intermediate SQL Tutorial", sequence=20, estimated_time_minutes=180, milestone_id=m3_da.id, step_type='Learning')
                    s3_3_da = Step(name="Advanced SQL Tutorial", sequence=30, estimated_time_minutes=180, milestone_id=m3_da.id, step_type='Learning')
                    s3_4_da = Step(name="Comprehensive SQL Tutorial (DataLemur)", sequence=40, estimated_time_minutes=300, milestone_id=m3_da.id, step_type='Learning')
                    s3_5_da = Step(name="Practice SQL Skills (DataLemur)", sequence=50, estimated_time_minutes=600, milestone_id=m3_da.id, step_type='Practice')
                    db.session.add_all([s3_1_da, s3_2_da, s3_3_da, s3_4_da, s3_5_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="SQL Basic Tutorial (Alex YT - Placeholder)", url="#placeholder_yt_link_6", resource_type="Video", step_id=s3_1_da.id))
                    resources_to_add_da.append(Resource(name="SQL Intermediate Tutorial (Alex YT - Placeholder)", url="#placeholder_yt_link_7", resource_type="Video", step_id=s3_2_da.id))
                    resources_to_add_da.append(Resource(name="Advanced SQL Tutorial (Alex YT - Placeholder)", url="#placeholder_yt_link_8", resource_type="Video", step_id=s3_3_da.id))
                    resources_to_add_da.append(Resource(name="SQL Tutorial", url="https://datalemur.com/sql-tutorial", resource_type="Tutorial", step_id=s3_4_da.id))
                    resources_to_add_da.append(Resource(name="SQL Practice Questions", url="https://datalemur.com/sql-interview-questions", resource_type="Practice", step_id=s3_5_da.id))

                    # Milestone 4: Data Visualization with Tableau
                    m4_da = Milestone(name="Data Visualization with Tableau", sequence=40, career_path_id=da_path.id); db.session.add(m4_da); db.session.flush()
                    s4_1_da = Step(name="Official Tableau Training", sequence=10, estimated_time_minutes=480, milestone_id=m4_da.id, step_type='Learning')
                    s4_2_da = Step(name="Tableau Beginner Tutorial", sequence=20, estimated_time_minutes=180, milestone_id=m4_da.id, step_type='Learning')
                    s4_3_da = Step(name="Guided Project: Tableau Dashboard", sequence=30, estimated_time_minutes=180, milestone_id=m4_da.id, step_type='Project')
                    s4_4_da = Step(name="Guided Project: Tableau Beginner Project", sequence=40, estimated_time_minutes=120, milestone_id=m4_da.id, step_type='Project')
                    db.session.add_all([s4_1_da, s4_2_da, s4_3_da, s4_4_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="Tableau Free Training Videos", url="https://www.tableau.com/learn/training", resource_type="Course", step_id=s4_1_da.id))
                    resources_to_add_da.append(Resource(name="Tableau for Beginners (Alex YT - Placeholder)", url="#placeholder_yt_link_9", resource_type="Video", step_id=s4_2_da.id))
                    resources_to_add_da.append(Resource(name="Creating Dashboards (Alex YT - Placeholder)", url="#placeholder_yt_link_11", resource_type="Project", step_id=s4_3_da.id))
                    resources_to_add_da.append(Resource(name="Tableau Beginner Project (Alex YT - Placeholder)", url="#placeholder_yt_link_12", resource_type="Project", step_id=s4_4_da.id))

                    # Milestone 5: Data Visualization with Power BI
                    m5_da = Milestone(name="Data Visualization with Power BI", sequence=50, career_path_id=da_path.id); db.session.add(m5_da); db.session.flush()
                    s5_1_da = Step(name="Learn Power BI Fundamentals (MS Learn)", sequence=10, estimated_time_minutes=600, milestone_id=m5_da.id, step_type='Learning')
                    s5_2_da = Step(name="Practice Power BI Skills", sequence=20, estimated_time_minutes=300, milestone_id=m5_da.id, step_type='Practice', description="Apply learnings from MS Learn modules by building sample dashboards.")
                    db.session.add_all([s5_1_da, s5_2_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="Microsoft Learn Power BI Modules (MS - Placeholder)", url="#placeholder_ms_learn_link_13", resource_type="Course", step_id=s5_1_da.id))

                    # Milestone 6: Programming with Python for Data Analysis
                    m6_da = Milestone(name="Programming with Python for Data Analysis", sequence=60, career_path_id=da_path.id); db.session.add(m6_da); db.session.flush()
                    s6_1_da = Step(name="Python for Beginners Tutorial", sequence=10, estimated_time_minutes=240, milestone_id=m6_da.id, step_type='Learning')
                    s6_2_da = Step(name="Introduction to Pandas & NumPy", sequence=20, estimated_time_minutes=480, milestone_id=m6_da.id, step_type='Learning')
                    s6_3_da = Step(name="Data Visualization with Matplotlib/Seaborn", sequence=30, estimated_time_minutes=300, milestone_id=m6_da.id, step_type='Learning')
                    s6_4_da = Step(name="Guided Project: Python Data Analysis", sequence=40, estimated_time_minutes=300, milestone_id=m6_da.id, step_type='Project')
                    s6_5_da = Step(name="Basic Web Scraping (Optional)", sequence=50, estimated_time_minutes=120, milestone_id=m6_da.id, step_type='Learning')
                    db.session.add_all([s6_1_da, s6_2_da, s6_3_da, s6_4_da, s6_5_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="Python for Beginners (Alex YT - Placeholder)", url="#placeholder_yt_link_14", resource_type="Video", step_id=s6_1_da.id))
                    resources_to_add_da.append(Resource(name="Pandas Tutorial (Data School)", url="https://www.dataschool.io/pandas-tutorial-beginner/", resource_type="Tutorial", step_id=s6_2_da.id))
                    resources_to_add_da.append(Resource(name="NumPy Quickstart", url="https://numpy.org/doc/stable/user/quickstart.html", resource_type="Documentation", step_id=s6_2_da.id))
                    resources_to_add_da.append(Resource(name="Matplotlib Pyplot Tutorial", url="https://matplotlib.org/stable/tutorials/introductory/pyplot.html", resource_type="Tutorial", step_id=s6_3_da.id))
                    resources_to_add_da.append(Resource(name="Seaborn Tutorial", url="https://seaborn.pydata.org/tutorial.html", resource_type="Tutorial", step_id=s6_3_da.id))
                    resources_to_add_da.append(Resource(name="Python Project (Alex YT - Placeholder)", url="#placeholder_yt_link_16", resource_type="Project", step_id=s6_4_da.id))
                    resources_to_add_da.append(Resource(name="Python Web Scraping Basics (Alex YT - Placeholder)", url="#placeholder_yt_link_15", resource_type="Video", step_id=s6_5_da.id))

                    # Milestone 7: Building Your Data Analytics Portfolio
                    m7_da = Milestone(name="Building Your Data Analytics Portfolio", sequence=70, career_path_id=da_path.id); db.session.add(m7_da); db.session.flush()
                    s7_1_da = Step(name="Learn Report Structuring", sequence=10, estimated_time_minutes=60, milestone_id=m7_da.id, step_type='Learning')
                    s7_2_da = Step(name="Choose & Set Up Portfolio Platform", sequence=20, estimated_time_minutes=120, milestone_id=m7_da.id, step_type='Setup')
                    s7_3_da = Step(name="Find Datasets for Projects", sequence=30, estimated_time_minutes=60, milestone_id=m7_da.id, step_type='Informational')
                    s7_4_da = Step(name="Complete & Document 2-3 Portfolio Projects", sequence=40, estimated_time_minutes=1200, milestone_id=m7_da.id, step_type='Project', description="Apply all learned skills (Excel, SQL, Viz Tool, Python) to real datasets.")
                    db.session.add_all([s7_1_da, s7_2_da, s7_3_da, s7_4_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="How I Structure My Data Analytics Reports (Medium - Placeholder)", url="#placeholder_medium_article_structuring", resource_type="Article", step_id=s7_1_da.id))
                    resources_to_add_da.append(Resource(name="How to use Kaggle (YT - Placeholder)", url="#placeholder_yt_link_20", resource_type="Guide", step_id=s7_2_da.id))
                    resources_to_add_da.append(Resource(name="How to use GitHub (YT - Placeholder)", url="#placeholder_yt_link_22", resource_type="Guide", step_id=s7_2_da.id))
                    resources_to_add_da.append(Resource(name="How to use Google Sites (YT - Placeholder)", url="#placeholder_yt_link_24", resource_type="Guide", step_id=s7_2_da.id))
                    resources_to_add_da.append(Resource(name="Kaggle Datasets", url="https://www.kaggle.com/datasets", resource_type="Resource", step_id=s7_3_da.id))
                    resources_to_add_da.append(Resource(name="Data.gov", url="https://data.gov/", resource_type="Resource", step_id=s7_3_da.id))

                    # Milestone 8: Gaining Practical Experience
                    m8_da = Milestone(name="Gaining Practical Experience", sequence=80, career_path_id=da_path.id); db.session.add(m8_da); db.session.flush()
                    s8_1_da = Step(name="Explore Virtual Internships", sequence=10, estimated_time_minutes=60, milestone_id=m8_da.id, step_type='Informational')
                    s8_2_da = Step(name="Complete 1-2 Relevant Virtual Internships", sequence=20, estimated_time_minutes=600, milestone_id=m8_da.id, step_type='Project') # Treat internships as projects
                    db.session.add_all([s8_1_da, s8_2_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="Forage Virtual Experiences (Forage - Placeholder)", url="#placeholder_forage_link", resource_type="Platform", step_id=s8_1_da.id))

                    # Milestone 9: Resume & Job Application Strategy
                    m9_da = Milestone(name="Resume & Job Application Strategy", sequence=90, career_path_id=da_path.id); db.session.add(m9_da); db.session.flush()
                    s9_1_da = Step(name="Craft Your Data Analyst Resume", sequence=10, estimated_time_minutes=180, milestone_id=m9_da.id, step_type='Informational')
                    s9_2_da = Step(name="Optimize Your LinkedIn Profile", sequence=20, estimated_time_minutes=120, milestone_id=m9_da.id, step_type='Informational')
                    db.session.add_all([s9_1_da, s9_2_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="How to create DA resume (YT - Placeholder)", url="#placeholder_yt_link_26", resource_type="Guide", step_id=s9_1_da.id))
                    resources_to_add_da.append(Resource(name="Free Resume Template (Placeholder)", url="#placeholder_bitly_link_resume", resource_type="Resource", step_id=s9_1_da.id))
                    resources_to_add_da.append(Resource(name="LinkedIn Job Tips 1 (YT - Placeholder)", url="#placeholder_yt_link_27", resource_type="Guide", step_id=s9_2_da.id))
                    resources_to_add_da.append(Resource(name="LinkedIn Job Tips 2 (YT - Placeholder)", url="#placeholder_yt_link_28", resource_type="Guide", step_id=s9_2_da.id))
                    resources_to_add_da.append(Resource(name="LinkedIn Optimization Thread (Twitter - Placeholder)", url="#placeholder_twitter_link_abiola", resource_type="Guide", step_id=s9_2_da.id))

                    # Milestone 10: Interview Preparation
                    m10_da = Milestone(name="Interview Preparation", sequence=100, career_path_id=da_path.id); db.session.add(m10_da); db.session.flush()
                    s10_1_da = Step(name="Learn Interview Red Flags & Remote Tips", sequence=10, estimated_time_minutes=60, milestone_id=m10_da.id, step_type='Informational')
                    s10_2_da = Step(name="Practice Data Analyst Interview Questions", sequence=20, estimated_time_minutes=300, milestone_id=m10_da.id, step_type='Practice')
                    db.session.add_all([s10_1_da, s10_2_da]); db.session.flush()
                    resources_to_add_da.append(Resource(name="Interview Red Flags Thread (Twitter - Placeholder)", url="#placeholder_twitter_link_dave", resource_type="Guide", step_id=s10_1_da.id))
                    resources_to_add_da.append(Resource(name="DA Interview Questions Thread (Twitter - Placeholder)", url="#placeholder_twitter_link_jess", resource_type="Guide", step_id=s10_2_da.id))

                    # --- Add all collected resources for DA ---
                    if resources_to_add_da:
                        db.session.add_all(resources_to_add_da)

                    db.session.commit()
                    print(f"Path '{da_path.name}' seeded successfully.")
                except Exception as e:
                    db.session.rollback()
                    print(f"Error seeding path '{da_path.name}': {e}")

            elif da_path:
                 print(f"Path '{da_path.name}' milestones already seem to exist. Skipping seeding.")
            else:
                 print("Data Analysis career path not found in DB, skipping seeding.")
            # --- <<< END DATA ANALYSIS SEEDING BLOCK >>> ---


            # --- Seed UX/UI Design Path ---
            print("Checking for UX/UI Design path seeding...")
            uxui_path = CareerPath.query.filter_by(name="UX/UI Design").first()

            if uxui_path and not Milestone.query.filter_by(career_path_id=uxui_path.id).first():
                print(f"Seeding path for '{uxui_path.name}'...")
                resources_to_add_ux = []
                try:
                    # Milestone 1: Introduction to UX/UI
                    m1_ux = Milestone(name="Introduction to UX/UI Design", sequence=10, career_path_id=uxui_path.id); db.session.add(m1_ux); db.session.flush()
                    s1_1_ux = Step(name="Understand UX vs UI", sequence=10, estimated_time_minutes=60, milestone_id=m1_ux.id, step_type='Learning')
                    s1_2_ux = Step(name="Explore UX/UI Career Paths & Roles", sequence=20, estimated_time_minutes=90, milestone_id=m1_ux.id, step_type='Learning')
                    s1_3_ux = Step(name="Learn the Design Thinking Process", sequence=30, estimated_time_minutes=120, milestone_id=m1_ux.id, step_type='Learning')
                    db.session.add_all([s1_1_ux, s1_2_ux, s1_3_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="UX vs UI Explained (CareerFoundry - Placeholder)", url="#placeholder_uxui_cf_article", resource_type="Article", step_id=s1_1_ux.id))
                    resources_to_add_ux.append(Resource(name="Google UX Design Certificate Intro (Coursera - Placeholder)", url="#placeholder_uxui_google_intro", resource_type="Course", step_id=s1_2_ux.id))
                    resources_to_add_ux.append(Resource(name="Design Thinking Overview (IDEO - Placeholder)", url="#placeholder_uxui_ideo_dt", resource_type="Resource", step_id=s1_3_ux.id))

                    # Milestone 2: Core Design Principles
                    m2_ux = Milestone(name="Core Design Principles", sequence=20, career_path_id=uxui_path.id); db.session.add(m2_ux); db.session.flush()
                    s2_1_ux = Step(name="Visual Hierarchy & Layout", sequence=10, estimated_time_minutes=120, milestone_id=m2_ux.id, step_type='Learning')
                    s2_2_ux = Step(name="Color Theory Basics", sequence=20, estimated_time_minutes=90, milestone_id=m2_ux.id, step_type='Learning')
                    s2_3_ux = Step(name="Typography Fundamentals", sequence=30, estimated_time_minutes=90, milestone_id=m2_ux.id, step_type='Learning')
                    s2_4_ux = Step(name="Usability Heuristics (Nielsen's 10)", sequence=40, estimated_time_minutes=120, milestone_id=m2_ux.id, step_type='Learning')
                    db.session.add_all([s2_1_ux, s2_2_ux, s2_3_ux, s2_4_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="Laws of UX (Website - Placeholder)", url="#placeholder_uxui_laws_ux", resource_type="Resource", step_id=s2_1_ux.id))
                    resources_to_add_ux.append(Resource(name="Color Theory (Interaction Design Foundation - Placeholder)", url="#placeholder_uxui_idf_color", resource_type="Article", step_id=s2_2_ux.id))
                    resources_to_add_ux.append(Resource(name="Typography Guide (Material Design - Placeholder)", url="#placeholder_uxui_material_type", resource_type="Guide", step_id=s2_3_ux.id))
                    resources_to_add_ux.append(Resource(name="10 Usability Heuristics (NN/g - Placeholder)", url="#placeholder_uxui_nng_heuristics", resource_type="Article", step_id=s2_4_ux.id))

                    # Milestone 3: User Research Fundamentals
                    m3_ux = Milestone(name="User Research Fundamentals", sequence=30, career_path_id=uxui_path.id); db.session.add(m3_ux); db.session.flush()
                    s3_1_ux = Step(name="Creating User Personas", sequence=10, estimated_time_minutes=120, milestone_id=m3_ux.id, step_type='Practice')
                    s3_2_ux = Step(name="Conducting User Interviews", sequence=20, estimated_time_minutes=180, milestone_id=m3_ux.id, step_type='Learning')
                    s3_3_ux = Step(name="Survey Design Basics", sequence=30, estimated_time_minutes=90, milestone_id=m3_ux.id, step_type='Learning')
                    s3_4_ux = Step(name="Introduction to Usability Testing", sequence=40, estimated_time_minutes=180, milestone_id=m3_ux.id, step_type='Learning')
                    db.session.add_all([s3_1_ux, s3_2_ux, s3_3_ux, s3_4_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="Personas Guide (Interaction Design Foundation - Placeholder)", url="#placeholder_uxui_idf_personas", resource_type="Article", step_id=s3_1_ux.id))
                    resources_to_add_ux.append(Resource(name="User Interview Guide (NN/g - Placeholder)", url="#placeholder_uxui_nng_interviews", resource_type="Article", step_id=s3_2_ux.id))
                    resources_to_add_ux.append(Resource(name="Survey Guide (SurveyMonkey - Placeholder)", url="#placeholder_uxui_sm_surveys", resource_type="Guide", step_id=s3_3_ux.id))
                    resources_to_add_ux.append(Resource(name="Usability Testing 101 (NN/g - Placeholder)", url="#placeholder_uxui_nng_testing", resource_type="Article", step_id=s3_4_ux.id))

                    # Milestone 4: IA & User Flows
                    m4_ux = Milestone(name="Information Architecture & User Flows", sequence=40, career_path_id=uxui_path.id); db.session.add(m4_ux); db.session.flush()
                    s4_1_ux = Step(name="Information Architecture Basics", sequence=10, estimated_time_minutes=120, milestone_id=m4_ux.id, step_type='Learning')
                    s4_2_ux = Step(name="Creating Sitemaps", sequence=20, estimated_time_minutes=90, milestone_id=m4_ux.id, step_type='Practice')
                    s4_3_ux = Step(name="Mapping User Flows", sequence=30, estimated_time_minutes=180, milestone_id=m4_ux.id, step_type='Practice')
                    s4_4_ux = Step(name="Introduction to Card Sorting", sequence=40, estimated_time_minutes=60, milestone_id=m4_ux.id, step_type='Learning')
                    db.session.add_all([s4_1_ux, s4_2_ux, s4_3_ux, s4_4_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="Complete Beginner's Guide to IA (UX Booth - Placeholder)", url="#placeholder_uxui_uxbooth_ia", resource_type="Article", step_id=s4_1_ux.id))
                    resources_to_add_ux.append(Resource(name="Sitemaps Tutorial (Figma - Placeholder)", url="#placeholder_uxui_figma_sitemaps", resource_type="Tutorial", step_id=s4_2_ux.id))
                    resources_to_add_ux.append(Resource(name="User Flow Guide (Adobe XD - Placeholder)", url="#placeholder_uxui_adobe_flows", resource_type="Guide", step_id=s4_3_ux.id))
                    resources_to_add_ux.append(Resource(name="Card Sorting Intro (NN/g - Placeholder)", url="#placeholder_uxui_nng_cardsort", resource_type="Article", step_id=s4_4_ux.id))

                    # Milestone 5: Wireframing & Prototyping
                    m5_ux = Milestone(name="Wireframing & Prototyping", sequence=50, career_path_id=uxui_path.id); db.session.add(m5_ux); db.session.flush()
                    s5_1_ux = Step(name="Understanding Wireframe Fidelity", sequence=10, estimated_time_minutes=60, milestone_id=m5_ux.id, step_type='Learning')
                    s5_2_ux = Step(name="Creating Low-Fidelity Wireframes", sequence=20, estimated_time_minutes=180, milestone_id=m5_ux.id, step_type='Practice')
                    s5_3_ux = Step(name="Building High-Fidelity Wireframes/Mockups", sequence=30, estimated_time_minutes=300, milestone_id=m5_ux.id, step_type='Practice')
                    s5_4_ux = Step(name="Creating Interactive Prototypes", sequence=40, estimated_time_minutes=300, milestone_id=m5_ux.id, step_type='Practice')
                    db.session.add_all([s5_1_ux, s5_2_ux, s5_3_ux, s5_4_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="Wireframing Guide (CareerFoundry - Placeholder)", url="#placeholder_uxui_cf_wireframe", resource_type="Guide", step_id=s5_1_ux.id))
                    resources_to_add_ux.append(Resource(name="Low-Fi Wireframing Tools (Blog - Placeholder)", url="#placeholder_uxui_blog_lowfi", resource_type="Article", step_id=s5_2_ux.id))
                    resources_to_add_ux.append(Resource(name="Hi-Fi Wireframing Tutorial (Figma YT - Placeholder)", url="#placeholder_uxui_figmayt_hifi", resource_type="Video", step_id=s5_3_ux.id))
                    resources_to_add_ux.append(Resource(name="Prototyping in Figma (Figma Docs - Placeholder)", url="#placeholder_uxui_figmadocs_proto", resource_type="Documentation", step_id=s5_4_ux.id))

                    # Milestone 6: Mastering Design Tools (Figma)
                    m6_ux = Milestone(name="Mastering Design Tools (Figma Focus)", sequence=60, career_path_id=uxui_path.id); db.session.add(m6_ux); db.session.flush()
                    s6_1_ux = Step(name="Figma Interface & Basics", sequence=10, estimated_time_minutes=240, milestone_id=m6_ux.id, step_type='Learning')
                    s6_2_ux = Step(name="Using Auto Layout", sequence=20, estimated_time_minutes=180, milestone_id=m6_ux.id, step_type='Learning')
                    s6_3_ux = Step(name="Working with Components & Variants", sequence=30, estimated_time_minutes=300, milestone_id=m6_ux.id, step_type='Learning')
                    s6_4_ux = Step(name="Exploring Figma Plugins", sequence=40, estimated_time_minutes=60, milestone_id=m6_ux.id, step_type='Learning')
                    s6_5_ux = Step(name="Practice Project in Figma", sequence=50, estimated_time_minutes=600, milestone_id=m6_ux.id, step_type='Project')
                    db.session.add_all([s6_1_ux, s6_2_ux, s6_3_ux, s6_4_ux, s6_5_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="Figma Beginners Tutorial (YT - Placeholder)", url="#placeholder_uxui_yt_figmabasic", resource_type="Video", step_id=s6_1_ux.id))
                    resources_to_add_ux.append(Resource(name="Figma Auto Layout Guide (Figma - Placeholder)", url="#placeholder_uxui_figma_autolayout", resource_type="Guide", step_id=s6_2_ux.id))
                    resources_to_add_ux.append(Resource(name="Figma Components Tutorial (YT - Placeholder)", url="#placeholder_uxui_yt_components", resource_type="Video", step_id=s6_3_ux.id))
                    resources_to_add_ux.append(Resource(name="Top Figma Plugins (Blog - Placeholder)", url="#placeholder_uxui_blog_plugins", resource_type="Article", step_id=s6_4_ux.id))
                    resources_to_add_ux.append(Resource(name="Design a Mobile App (YT Project - Placeholder)", url="#placeholder_uxui_yt_project", resource_type="Project", step_id=s6_5_ux.id))

                    # Milestone 7: Visual Design & UI Details
                    m7_ux = Milestone(name="Visual Design & UI Details", sequence=70, career_path_id=uxui_path.id); db.session.add(m7_ux); db.session.flush()
                    s7_1_ux = Step(name="Creating Style Guides", sequence=10, estimated_time_minutes=120, milestone_id=m7_ux.id, step_type='Practice')
                    s7_2_ux = Step(name="Introduction to Design Systems", sequence=20, estimated_time_minutes=180, milestone_id=m7_ux.id, step_type='Learning')
                    s7_3_ux = Step(name="Web Accessibility Basics (WCAG)", sequence=30, estimated_time_minutes=240, milestone_id=m7_ux.id, step_type='Learning')
                    s7_4_ux = Step(name="Using UI Kits & Iconography", sequence=40, estimated_time_minutes=120, milestone_id=m7_ux.id, step_type='Practice')
                    db.session.add_all([s7_1_ux, s7_2_ux, s7_3_ux, s7_4_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="Style Guide Examples (Blog - Placeholder)", url="#placeholder_uxui_blog_styleguide", resource_type="Article", step_id=s7_1_ux.id))
                    resources_to_add_ux.append(Resource(name="Design Systems Intro (InVision - Placeholder)", url="#placeholder_uxui_invision_ds", resource_type="Article", step_id=s7_2_ux.id))
                    resources_to_add_ux.append(Resource(name="WCAG Overview (W3C - Placeholder)", url="#placeholder_uxui_w3c_wcag", resource_type="Documentation", step_id=s7_3_ux.id))
                    resources_to_add_ux.append(Resource(name="Free UI Kits for Figma (Resource - Placeholder)", url="#placeholder_uxui_resource_uikits", resource_type="Resource", step_id=s7_4_ux.id))

                    # Milestone 8: Building Your UX/UI Portfolio
                    m8_ux = Milestone(name="Building Your UX/UI Portfolio", sequence=80, career_path_id=uxui_path.id); db.session.add(m8_ux); db.session.flush()
                    s8_1_ux = Step(name="Selecting Portfolio Projects", sequence=10, estimated_time_minutes=60, milestone_id=m8_ux.id, step_type='Informational')
                    s8_2_ux = Step(name="Structuring a UX Case Study", sequence=20, estimated_time_minutes=180, milestone_id=m8_ux.id, step_type='Learning')
                    s8_3_ux = Step(name="Choosing a Portfolio Platform (Behance, Dribbble, etc.)", sequence=30, estimated_time_minutes=120, milestone_id=m8_ux.id, step_type='Setup')
                    s8_4_ux = Step(name="Create & Refine 2-3 Case Studies", sequence=40, estimated_time_minutes=1200, milestone_id=m8_ux.id, step_type='Project')
                    db.session.add_all([s8_1_ux, s8_2_ux, s8_3_ux, s8_4_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="How to Choose Projects (Blog - Placeholder)", url="#placeholder_uxui_blog_projects", resource_type="Article", step_id=s8_1_ux.id))
                    resources_to_add_ux.append(Resource(name="UX Case Study Guide (Medium - Placeholder)", url="#placeholder_uxui_medium_casestudy", resource_type="Article", step_id=s8_2_ux.id))
                    resources_to_add_ux.append(Resource(name="Behance", url="https://www.behance.net/", resource_type="Platform", step_id=s8_3_ux.id))
                    resources_to_add_ux.append(Resource(name="Dribbble", url="https://dribbble.com/", resource_type="Platform", step_id=s8_3_ux.id))

                    # Milestone 9: Collaboration & Handoff
                    m9_ux = Milestone(name="Collaboration & Handoff", sequence=90, career_path_id=uxui_path.id); db.session.add(m9_ux); db.session.flush()
                    s9_1_ux = Step(name="Working Effectively with Developers", sequence=10, estimated_time_minutes=120, milestone_id=m9_ux.id, step_type='Learning')
                    s9_2_ux = Step(name="Creating Design Specifications", sequence=20, estimated_time_minutes=90, milestone_id=m9_ux.id, step_type='Practice')
                    s9_3_ux = Step(name="Using Handoff Features (Figma Inspect)", sequence=30, estimated_time_minutes=60, milestone_id=m9_ux.id, step_type='Learning')
                    db.session.add_all([s9_1_ux, s9_2_ux, s9_3_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="Designer/Developer Collaboration (Abstract - Placeholder)", url="#placeholder_uxui_abstract_collab", resource_type="Article", step_id=s9_1_ux.id))
                    resources_to_add_ux.append(Resource(name="Design Specs Guide (Zeplin Blog - Placeholder)", url="#placeholder_uxui_zeplin_specs", resource_type="Article", step_id=s9_2_ux.id))
                    resources_to_add_ux.append(Resource(name="Figma Inspect Mode (Figma Docs - Placeholder)", url="#placeholder_uxui_figmadocs_inspect", resource_type="Documentation", step_id=s9_3_ux.id))

                    # Milestone 10: Job Search & Interview Prep (UX/UI)
                    m10_ux = Milestone(name="Job Search & Interview Prep (UX/UI)", sequence=100, career_path_id=uxui_path.id); db.session.add(m10_ux); db.session.flush()
                    s10_1_ux = Step(name="Tailoring Your UX/UI Resume", sequence=10, estimated_time_minutes=120, milestone_id=m10_ux.id, step_type='Informational')
                    s10_2_ux = Step(name="Preparing Your Portfolio for Review", sequence=20, estimated_time_minutes=180, milestone_id=m10_ux.id, step_type='Practice')
                    s10_3_ux = Step(name="Common UX/UI Interview Questions", sequence=30, estimated_time_minutes=180, milestone_id=m10_ux.id, step_type='Practice')
                    s10_4_ux = Step(name="Understanding Design Challenges & Whiteboard Tests", sequence=40, estimated_time_minutes=240, milestone_id=m10_ux.id, step_type='Learning')
                    db.session.add_all([s10_1_ux, s10_2_ux, s10_3_ux, s10_4_ux]); db.session.flush()
                    resources_to_add_ux.append(Resource(name="UX Resume Tips (NN/g - Placeholder)", url="#placeholder_uxui_nng_resume", resource_type="Article", step_id=s10_1_ux.id))
                    resources_to_add_ux.append(Resource(name="Portfolio Review Prep (Medium - Placeholder)", url="#placeholder_uxui_medium_portfolioreview", resource_type="Article", step_id=s10_2_ux.id))
                    resources_to_add_ux.append(Resource(name="UX Interview Questions (Toptal - Placeholder)", url="#placeholder_uxui_toptal_interview", resource_type="Article", step_id=s10_3_ux.id))
                    resources_to_add_ux.append(Resource(name="Whiteboard Challenge Guide (YT - Placeholder)", url="#placeholder_uxui_yt_whiteboard", resource_type="Video", step_id=s10_4_ux.id))

                    # --- Add all collected resources for UX/UI ---
                    if resources_to_add_ux:
                        db.session.add_all(resources_to_add_ux)

                    db.session.commit() # Commit after seeding path
                    print(f"Path '{uxui_path.name}' seeded successfully.")

                except Exception as e:
                    db.session.rollback()
                    print(f"Error seeding path '{uxui_path.name}': {e}")

            elif uxui_path:
                 print(f"Path '{uxui_path.name}' milestones already seem to exist. Skipping seeding.")
            else:
                 print("UX/UI Design career path not found in DB, skipping seeding.")
            # --- <<< END UX/UI SEEDING BLOCK >>> ---


            # --- <<< ADD CYBERSECURITY SEEDING BLOCK >>> ---
            print("Checking for Cybersecurity path seeding...")
            cyber_path = CareerPath.query.filter_by(name="Cybersecurity").first()

            if cyber_path and not Milestone.query.filter_by(career_path_id=cyber_path.id).first():
                print(f"Seeding path for '{cyber_path.name}'...")
                resources_to_add_cyber = []
                try:
                    # Milestone 1: IT & Networking Foundations
                    m1_cy = Milestone(name="Foundational IT & Networking Concepts", sequence=10, career_path_id=cyber_path.id); db.session.add(m1_cy); db.session.flush()
                    s1_1_cy = Step(name="Understand Computer Hardware & OS Basics", sequence=10, estimated_time_minutes=300, milestone_id=m1_cy.id, step_type='Learning')
                    s1_2_cy = Step(name="Learn the OSI & TCP/IP Models", sequence=20, estimated_time_minutes=240, milestone_id=m1_cy.id, step_type='Learning')
                    s1_3_cy = Step(name="IP Addressing (IPv4/IPv6) & Subnetting Basics", sequence=30, estimated_time_minutes=300, milestone_id=m1_cy.id, step_type='Learning')
                    s1_4_cy = Step(name="Common Network Protocols (HTTP, DNS, DHCP, etc.)", sequence=40, estimated_time_minutes=180, milestone_id=m1_cy.id, step_type='Learning')
                    db.session.add_all([s1_1_cy, s1_2_cy, s1_3_cy, s1_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="CompTIA IT Fundamentals (Exam Objectives)", url="#placeholder_cyber_itf_objectives", resource_type="Guide", step_id=s1_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="OSI Model Explained (Video)", url="#placeholder_cyber_yt_osi", resource_type="Video", step_id=s1_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="IP Addressing Tutorial (Website)", url="#placeholder_cyber_web_ip", resource_type="Tutorial", step_id=s1_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Network Protocols Overview (Article)", url="#placeholder_cyber_article_protocols", resource_type="Article", step_id=s1_4_cy.id))

                    # Milestone 2: Intro to Cybersecurity Principles
                    m2_cy = Milestone(name="Introduction to Cybersecurity Principles", sequence=20, career_path_id=cyber_path.id); db.session.add(m2_cy); db.session.flush()
                    s2_1_cy = Step(name="Learn the CIA Triad (Confidentiality, Integrity, Availability)", sequence=10, estimated_time_minutes=60, milestone_id=m2_cy.id, step_type='Learning')
                    s2_2_cy = Step(name="Identify Common Cyber Threats & Attack Vectors", sequence=20, estimated_time_minutes=120, milestone_id=m2_cy.id, step_type='Learning')
                    s2_3_cy = Step(name="Understand Basic Cryptography Concepts (Encryption, Hashing)", sequence=30, estimated_time_minutes=180, milestone_id=m2_cy.id, step_type='Learning')
                    s2_4_cy = Step(name="Develop a Security Mindset", sequence=40, estimated_time_minutes=60, milestone_id=m2_cy.id, step_type='Learning')
                    db.session.add_all([s2_1_cy, s2_2_cy, s2_3_cy, s2_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="CIA Triad Explained (Article)", url="#placeholder_cyber_article_cia", resource_type="Article", step_id=s2_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Common Cyber Threats (Cybrary - Placeholder)", url="#placeholder_cyber_cybrary_threats", resource_type="Resource", step_id=s2_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Cryptography Basics (Khan Academy - Placeholder)", url="#placeholder_cyber_khan_crypto", resource_type="Course", step_id=s2_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Thinking Like an Attacker (Blog)", url="#placeholder_cyber_blog_mindset", resource_type="Article", step_id=s2_4_cy.id))

                    # Milestone 3: Operating Systems Security
                    m3_cy = Milestone(name="Operating Systems Security (Linux/Windows)", sequence=30, career_path_id=cyber_path.id); db.session.add(m3_cy); db.session.flush()
                    s3_1_cy = Step(name="Linux Command Line Basics", sequence=10, estimated_time_minutes=300, milestone_id=m3_cy.id, step_type='Learning')
                    s3_2_cy = Step(name="Linux File Permissions & Users", sequence=20, estimated_time_minutes=180, milestone_id=m3_cy.id, step_type='Learning')
                    s3_3_cy = Step(name="Windows Security Basics (Users, Permissions, Policies)", sequence=30, estimated_time_minutes=240, milestone_id=m3_cy.id, step_type='Learning')
                    s3_4_cy = Step(name="OS Hardening Concepts", sequence=40, estimated_time_minutes=120, milestone_id=m3_cy.id, step_type='Learning')
                    db.session.add_all([s3_1_cy, s3_2_cy, s3_3_cy, s3_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Linux Journey (Website)", url="#placeholder_cyber_linuxjourney", resource_type="Tutorial", step_id=s3_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Linux Permissions Explained (Article)", url="#placeholder_cyber_article_linuxperm", resource_type="Article", step_id=s3_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Windows Security Settings (MS Docs)", url="#placeholder_cyber_msdocs_winsec", resource_type="Documentation", step_id=s3_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="CIS Benchmarks Intro", url="#placeholder_cyber_cis_intro", resource_type="Resource", step_id=s3_4_cy.id))

                    # Milestone 4: Network Security Fundamentals
                    m4_cy = Milestone(name="Network Security Fundamentals", sequence=40, career_path_id=cyber_path.id); db.session.add(m4_cy); db.session.flush()
                    s4_1_cy = Step(name="Firewall Concepts & Rule Basics", sequence=10, estimated_time_minutes=180, milestone_id=m4_cy.id, step_type='Learning')
                    s4_2_cy = Step(name="VPN Technologies Overview (IPSec, SSL/TLS)", sequence=20, estimated_time_minutes=120, milestone_id=m4_cy.id, step_type='Learning')
                    s4_3_cy = Step(name="Intrusion Detection/Prevention Systems (IDS/IPS)", sequence=30, estimated_time_minutes=120, milestone_id=m4_cy.id, step_type='Learning')
                    s4_4_cy = Step(name="Wireless Security (WPA2/WPA3, Best Practices)", sequence=40, estimated_time_minutes=90, milestone_id=m4_cy.id, step_type='Learning')
                    db.session.add_all([s4_1_cy, s4_2_cy, s4_3_cy, s4_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Firewalls Explained (Video)", url="#placeholder_cyber_yt_firewall", resource_type="Video", step_id=s4_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="How VPNs Work (Article)", url="#placeholder_cyber_article_vpn", resource_type="Article", step_id=s4_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="IDS vs IPS (Article)", url="#placeholder_cyber_article_idsips", resource_type="Article", step_id=s4_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Wi-Fi Security Guide", url="#placeholder_cyber_guide_wifi", resource_type="Guide", step_id=s4_4_cy.id))

                    # Milestone 5: Security Tools & Technologies Intro
                    m5_cy = Milestone(name="Security Tools & Technologies Intro", sequence=50, career_path_id=cyber_path.id); db.session.add(m5_cy); db.session.flush()
                    s5_1_cy = Step(name="Network Scanning with Nmap (Basics)", sequence=10, estimated_time_minutes=240, milestone_id=m5_cy.id, step_type='Practice')
                    s5_2_cy = Step(name="Packet Analysis with Wireshark (Basics)", sequence=20, estimated_time_minutes=300, milestone_id=m5_cy.id, step_type='Practice')
                    s5_3_cy = Step(name="Vulnerability Scanner Concepts (e.g., Nessus, OpenVAS)", sequence=30, estimated_time_minutes=120, milestone_id=m5_cy.id, step_type='Learning')
                    s5_4_cy = Step(name="SIEM Introduction (e.g., Splunk Free, ELK Stack)", sequence=40, estimated_time_minutes=180, milestone_id=m5_cy.id, step_type='Learning')
                    db.session.add_all([s5_1_cy, s5_2_cy, s5_3_cy, s5_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Nmap Basics (TryHackMe - Placeholder)", url="#placeholder_cyber_thm_nmap", resource_type="Practice", step_id=s5_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Wireshark Tutorial (Video)", url="#placeholder_cyber_yt_wireshark", resource_type="Video", step_id=s5_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Vulnerability Scanning Overview (Article)", url="#placeholder_cyber_article_vulnscan", resource_type="Article", step_id=s5_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="What is SIEM? (Splunk)", url="#placeholder_cyber_splunk_siem", resource_type="Article", step_id=s5_4_cy.id))

                    # Milestone 6: Threats, Vulnerabilities & Risk Management
                    m6_cy = Milestone(name="Threats, Vulnerabilities & Risk Management", sequence=60, career_path_id=cyber_path.id); db.session.add(m6_cy); db.session.flush()
                    s6_1_cy = Step(name="Common Malware Types (Virus, Worm, Trojan, Ransomware)", sequence=10, estimated_time_minutes=120, milestone_id=m6_cy.id, step_type='Learning')
                    s6_2_cy = Step(name="Social Engineering Tactics (Phishing, Pretexting)", sequence=20, estimated_time_minutes=90, milestone_id=m6_cy.id, step_type='Learning')
                    s6_3_cy = Step(name="OWASP Top 10 Web Vulnerabilities Awareness", sequence=30, estimated_time_minutes=180, milestone_id=m6_cy.id, step_type='Learning')
                    s6_4_cy = Step(name="Risk Management Concepts (Assessment, Mitigation)", sequence=40, estimated_time_minutes=120, milestone_id=m6_cy.id, step_type='Learning')
                    db.session.add_all([s6_1_cy, s6_2_cy, s6_3_cy, s6_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Malware Types Explained (Malwarebytes)", url="#placeholder_cyber_mb_malware", resource_type="Resource", step_id=s6_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Social Engineering Attacks (Article)", url="#placeholder_cyber_article_soceng", resource_type="Article", step_id=s6_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="OWASP Top 10 Project", url="#placeholder_cyber_owasp_top10", resource_type="Resource", step_id=s6_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Intro to IT Risk Management (Video)", url="#placeholder_cyber_yt_risk", resource_type="Video", step_id=s6_4_cy.id))

                    # Milestone 7: Ethical Hacking Concepts
                    m7_cy = Milestone(name="Introduction to Ethical Hacking Concepts", sequence=70, career_path_id=cyber_path.id); db.session.add(m7_cy); db.session.flush()
                    s7_1_cy = Step(name="Phases of Penetration Testing", sequence=10, estimated_time_minutes=90, milestone_id=m7_cy.id, step_type='Learning')
                    s7_2_cy = Step(name="Reconnaissance Techniques (Passive/Active)", sequence=20, estimated_time_minutes=180, milestone_id=m7_cy.id, step_type='Learning')
                    s7_3_cy = Step(name="Basic Exploitation Concepts (Metasploit Intro)", sequence=30, estimated_time_minutes=240, milestone_id=m7_cy.id, step_type='Learning')
                    s7_4_cy = Step(name="Ethical Considerations & Reporting", sequence=40, estimated_time_minutes=60, milestone_id=m7_cy.id, step_type='Informational')
                    db.session.add_all([s7_1_cy, s7_2_cy, s7_3_cy, s7_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Pentest Phases Explained (Article)", url="#placeholder_cyber_article_pentest", resource_type="Article", step_id=s7_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Google Dorking / OSINT Basics", url="#placeholder_cyber_guide_osint", resource_type="Guide", step_id=s7_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Metasploit Unleashed (OffSec)", url="#placeholder_cyber_offsec_msf", resource_type="Resource", step_id=s7_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Ethical Hacking Ethics (Article)", url="#placeholder_cyber_article_ethics", resource_type="Article", step_id=s7_4_cy.id))

                    # Milestone 8: Security Operations & Incident Response Basics
                    m8_cy = Milestone(name="Security Operations & Incident Response Basics", sequence=80, career_path_id=cyber_path.id); db.session.add(m8_cy); db.session.flush()
                    s8_1_cy = Step(name="Understanding Log Sources & Types", sequence=10, estimated_time_minutes=120, milestone_id=m8_cy.id, step_type='Learning')
                    s8_2_cy = Step(name="Basic Log Analysis Techniques", sequence=20, estimated_time_minutes=240, milestone_id=m8_cy.id, step_type='Practice')
                    s8_3_cy = Step(name="Incident Response Lifecycle (e.g., PICERL)", sequence=30, estimated_time_minutes=180, milestone_id=m8_cy.id, step_type='Learning')
                    s8_4_cy = Step(name="SOC Analyst Role Overview", sequence=40, estimated_time_minutes=60, milestone_id=m8_cy.id, step_type='Informational')
                    db.session.add_all([s8_1_cy, s8_2_cy, s8_3_cy, s8_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Common Log Sources (Article)", url="#placeholder_cyber_article_logs", resource_type="Article", step_id=s8_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Log Analysis Tutorial (Video)", url="#placeholder_cyber_yt_loganalysis", resource_type="Video", step_id=s8_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Incident Response Steps (SANS)", url="#placeholder_cyber_sans_ir", resource_type="Guide", step_id=s8_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Day in the Life of a SOC Analyst (YT)", url="#placeholder_cyber_yt_soc", resource_type="Video", step_id=s8_4_cy.id))

                    # Milestone 9: Compliance & Frameworks Awareness
                    m9_cy = Milestone(name="Compliance & Frameworks Awareness", sequence=90, career_path_id=cyber_path.id); db.session.add(m9_cy); db.session.flush()
                    s9_1_cy = Step(name="Introduction to NIST Cybersecurity Framework (CSF)", sequence=10, estimated_time_minutes=120, milestone_id=m9_cy.id, step_type='Learning')
                    s9_2_cy = Step(name="Overview of ISO 27001/27002", sequence=20, estimated_time_minutes=90, milestone_id=m9_cy.id, step_type='Learning')
                    s9_3_cy = Step(name="Data Privacy Awareness (GDPR/NDPR Basics)", sequence=30, estimated_time_minutes=90, milestone_id=m9_cy.id, step_type='Learning')
                    db.session.add_all([s9_1_cy, s9_2_cy, s9_3_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="NIST CSF Overview (NIST)", url="#placeholder_cyber_nist_csf", resource_type="Resource", step_id=s9_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="ISO 27001 Explained (Article)", url="#placeholder_cyber_article_iso", resource_type="Article", step_id=s9_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="NDPR Overview (NITDA - Placeholder)", url="#placeholder_cyber_nitda_ndpr", resource_type="Resource", step_id=s9_3_cy.id))

                    # Milestone 10: Career Prep & Certifications (Security+)
                    m10_cy = Milestone(name="Career Prep & Certifications (Security+ Focus)", sequence=100, career_path_id=cyber_path.id); db.session.add(m10_cy); db.session.flush()
                    s10_1_cy = Step(name="Review CompTIA Security+ Exam Objectives", sequence=10, estimated_time_minutes=120, milestone_id=m10_cy.id, step_type='Informational')
                    s10_2_cy = Step(name="Study Key Security+ Domains (Using Free Resources)", sequence=20, estimated_time_minutes=2400, milestone_id=m10_cy.id, step_type='Certificate') # Long study time
                    s10_3_cy = Step(name="Cybersecurity Resume & Portfolio Tips", sequence=30, estimated_time_minutes=180, milestone_id=m10_cy.id, step_type='Informational')
                    s10_4_cy = Step(name="Practice Security Interview Questions", sequence=40, estimated_time_minutes=300, milestone_id=m10_cy.id, step_type='Practice')
                    db.session.add_all([s10_1_cy, s10_2_cy, s10_3_cy, s10_4_cy]); db.session.flush()
                    resources_to_add_cyber.append(Resource(name="Security+ SY0-701 Objectives (CompTIA)", url="#placeholder_cyber_comptia_secplus", resource_type="Guide", step_id=s10_1_cy.id))
                    resources_to_add_cyber.append(Resource(name="Professor Messer Security+ Training (YT)", url="#placeholder_cyber_yt_messer", resource_type="Course", step_id=s10_2_cy.id))
                    resources_to_add_cyber.append(Resource(name="Cybersecurity Resume Guide (Blog)", url="#placeholder_cyber_blog_resume", resource_type="Article", step_id=s10_3_cy.id))
                    resources_to_add_cyber.append(Resource(name="Security Analyst Interview Qs (Article)", url="#placeholder_cyber_article_interview", resource_type="Article", step_id=s10_4_cy.id))

                    # --- Add all collected resources for Cyber ---
                    if resources_to_add_cyber:
                        db.session.add_all(resources_to_add_cyber)

                    db.session.commit() # Commit after seeding path
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
                try:
                    # Milestone 1: Programming Fundamentals (Python)
                    m1_swe = Milestone(name="Programming Fundamentals (Python)", sequence=10, career_path_id=swe_path.id); db.session.add(m1_swe); db.session.flush()
                    s1_1_swe = Step(name="Python Setup & Basic Syntax (Variables, Types, Operators)", sequence=10, estimated_time_minutes=180, milestone_id=m1_swe.id, step_type='Setup')
                    s1_2_swe = Step(name="Control Flow (If/Else, For/While Loops)", sequence=20, estimated_time_minutes=240, milestone_id=m1_swe.id, step_type='Learning')
                    s1_3_swe = Step(name="Functions & Modules", sequence=30, estimated_time_minutes=300, milestone_id=m1_swe.id, step_type='Learning')
                    s1_4_swe = Step(name="Basic Object-Oriented Programming (Classes, Objects)", sequence=40, estimated_time_minutes=300, milestone_id=m1_swe.id, step_type='Learning')
                    s1_5_swe = Step(name="Error Handling (Try/Except)", sequence=50, estimated_time_minutes=120, milestone_id=m1_swe.id, step_type='Learning')
                    db.session.add_all([s1_1_swe, s1_2_swe, s1_3_swe, s1_4_swe, s1_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Official Python Tutorial", url="https://docs.python.org/3/tutorial/", resource_type="Documentation", step_id=s1_1_swe.id)) # Official link
                    resources_to_add_swe.append(Resource(name="Python Control Flow (Real Python)", url="#placeholder_swe_rp_control", resource_type="Article", step_id=s1_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Python Functions Guide (Video)", url="#placeholder_swe_yt_functions", resource_type="Video", step_id=s1_3_swe.id))
                    resources_to_add_swe.append(Resource(name="Python OOP Basics (Article)", url="#placeholder_swe_article_oop", resource_type="Article", step_id=s1_4_swe.id))
                    resources_to_add_swe.append(Resource(name="Python Exceptions Handling", url="#placeholder_swe_docs_exceptions", resource_type="Documentation", step_id=s1_5_swe.id))

                    # Milestone 2: Data Structures & Algorithms Basics
                    m2_swe = Milestone(name="Data Structures & Algorithms Basics", sequence=20, career_path_id=swe_path.id); db.session.add(m2_swe); db.session.flush()
                    s2_1_swe = Step(name="Common Data Structures (List, Dict, Set, Tuple)", sequence=10, estimated_time_minutes=180, milestone_id=m2_swe.id, step_type='Learning')
                    s2_2_swe = Step(name="Understanding Time & Space Complexity (Big O)", sequence=20, estimated_time_minutes=180, milestone_id=m2_swe.id, step_type='Learning')
                    s2_3_swe = Step(name="Basic Algorithms: Linear/Binary Search", sequence=30, estimated_time_minutes=120, milestone_id=m2_swe.id, step_type='Learning')
                    s2_4_swe = Step(name="Basic Algorithms: Bubble/Selection/Insertion Sort", sequence=40, estimated_time_minutes=180, milestone_id=m2_swe.id, step_type='Learning')
                    s2_5_swe = Step(name="Practice Problems (e.g., LeetCode Easy)", sequence=50, estimated_time_minutes=600, milestone_id=m2_swe.id, step_type='Practice')
                    db.session.add_all([s2_1_swe, s2_2_swe, s2_3_swe, s2_4_swe, s2_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Python Data Structures In-Depth (Article)", url="#placeholder_swe_article_ds", resource_type="Article", step_id=s2_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Big O Notation Explained (Video)", url="#placeholder_swe_yt_bigo", resource_type="Video", step_id=s2_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Searching Algorithms (GeeksForGeeks)", url="#placeholder_swe_gfg_search", resource_type="Article", step_id=s2_3_swe.id))
                    resources_to_add_swe.append(Resource(name="Sorting Algorithms Visualized", url="#placeholder_swe_web_sortviz", resource_type="Resource", step_id=s2_4_swe.id))
                    resources_to_add_swe.append(Resource(name="LeetCode", url="https://leetcode.com/problemset/all/?difficulty=EASY&page=1", resource_type="Platform", step_id=s2_5_swe.id))

                    # Milestone 3: Version Control (Git)
                    m3_swe = Milestone(name="Version Control with Git & GitHub", sequence=30, career_path_id=swe_path.id); db.session.add(m3_swe); db.session.flush()
                    s3_1_swe = Step(name="Install & Configure Git", sequence=10, estimated_time_minutes=60, milestone_id=m3_swe.id, step_type='Setup')
                    s3_2_swe = Step(name="Core Git Commands (init, add, commit, status, log)", sequence=20, estimated_time_minutes=180, milestone_id=m3_swe.id, step_type='Practice')
                    s3_3_swe = Step(name="Branching & Merging Basics", sequence=30, estimated_time_minutes=180, milestone_id=m3_swe.id, step_type='Practice')
                    s3_4_swe = Step(name="Working with Remote Repositories (GitHub - clone, push, pull, fetch)", sequence=40, estimated_time_minutes=240, milestone_id=m3_swe.id, step_type='Practice')
                    s3_5_swe = Step(name="Understanding Pull Requests", sequence=50, estimated_time_minutes=60, milestone_id=m3_swe.id, step_type='Learning')
                    db.session.add_all([s3_1_swe, s3_2_swe, s3_3_swe, s3_4_swe, s3_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Pro Git Book (Free Online)", url="https://git-scm.com/book/en/v2", resource_type="Book", step_id=s3_1_swe.id)) # Covers all steps
                    resources_to_add_swe.append(Resource(name="Git Tutorial (Atlassian)", url="https://www.atlassian.com/git/tutorials", resource_type="Tutorial", step_id=s3_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Learn Git Branching (Interactive)", url="https://learngitbranching.js.org/", resource_type="Practice", step_id=s3_3_swe.id))
                    resources_to_add_swe.append(Resource(name="GitHub Docs - Working with Remotes", url="#placeholder_swe_github_remotes", resource_type="Documentation", step_id=s3_4_swe.id))
                    resources_to_add_swe.append(Resource(name="Understanding Pull Requests (GitHub)", url="https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/about-pull-requests", resource_type="Guide", step_id=s3_5_swe.id))

                    # Milestone 4: Web Concepts
                    m4_swe = Milestone(name="Introduction to Web Concepts", sequence=40, career_path_id=swe_path.id); db.session.add(m4_swe); db.session.flush()
                    s4_1_swe = Step(name="How the Web Works (Client/Server, HTTP/HTTPS)", sequence=10, estimated_time_minutes=120, milestone_id=m4_swe.id, step_type='Learning')
                    s4_2_swe = Step(name="HTML Basics (Structure, Tags)", sequence=20, estimated_time_minutes=240, milestone_id=m4_swe.id, step_type='Learning')
                    s4_3_swe = Step(name="CSS Basics (Selectors, Box Model, Layout)", sequence=30, estimated_time_minutes=300, milestone_id=m4_swe.id, step_type='Learning')
                    s4_4_swe = Step(name="JavaScript Basics (Variables, Functions, DOM Manipulation, Events)", sequence=40, estimated_time_minutes=480, milestone_id=m4_swe.id, step_type='Learning')
                    db.session.add_all([s4_1_swe, s4_2_swe, s4_3_swe, s4_4_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="How the Internet Works (Video - Placeholder)", url="#placeholder_swe_yt_webworks", resource_type="Video", step_id=s4_1_swe.id))
                    resources_to_add_swe.append(Resource(name="HTML Tutorial (MDN)", url="https://developer.mozilla.org/en-US/docs/Web/HTML", resource_type="Tutorial", step_id=s4_2_swe.id))
                    resources_to_add_swe.append(Resource(name="CSS Tutorial (MDN)", url="https://developer.mozilla.org/en-US/docs/Web/CSS", resource_type="Tutorial", step_id=s4_3_swe.id))
                    resources_to_add_swe.append(Resource(name="JavaScript Tutorial (MDN)", url="https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide", resource_type="Tutorial", step_id=s4_4_swe.id))

                    # Milestone 5: Backend Development Framework (Flask)
                    m5_swe = Milestone(name="Backend Development Framework (Flask)", sequence=50, career_path_id=swe_path.id); db.session.add(m5_swe); db.session.flush()
                    s5_1_swe = Step(name="Flask Setup & Basic Routing", sequence=10, estimated_time_minutes=180, milestone_id=m5_swe.id, step_type='Learning')
                    s5_2_swe = Step(name="Using Templates (Jinja2)", sequence=20, estimated_time_minutes=180, milestone_id=m5_swe.id, step_type='Learning')
                    s5_3_swe = Step(name="Handling Forms (Flask-WTF)", sequence=30, estimated_time_minutes=240, milestone_id=m5_swe.id, step_type='Practice')
                    s5_4_swe = Step(name="Request Object & Response Cycle", sequence=40, estimated_time_minutes=120, milestone_id=m5_swe.id, step_type='Learning')
                    s5_5_swe = Step(name="Introduction to Blueprints (Structuring Larger Apps)", sequence=50, estimated_time_minutes=120, milestone_id=m5_swe.id, step_type='Learning')
                    db.session.add_all([s5_1_swe, s5_2_swe, s5_3_swe, s5_4_swe, s5_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Flask Quickstart (Official Docs)", url="https://flask.palletsprojects.com/en/3.0.x/quickstart/", resource_type="Documentation", step_id=s5_1_swe.id)) # Covers several steps
                    resources_to_add_swe.append(Resource(name="Flask Templates Guide (Jinja)", url="https://flask.palletsprojects.com/en/3.0.x/templating/", resource_type="Documentation", step_id=s5_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask-WTF Forms Guide", url="https://flask-wtf.readthedocs.io/en/1.2.x/quickstart/", resource_type="Documentation", step_id=s5_3_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask Request Object", url="https://flask.palletsprojects.com/en/3.0.x/reqcontext/", resource_type="Documentation", step_id=s5_4_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask Blueprints", url="https://flask.palletsprojects.com/en/3.0.x/blueprints/", resource_type="Documentation", step_id=s5_5_swe.id))

                    # Milestone 6: Databases & ORMs
                    m6_swe = Milestone(name="Databases & ORMs", sequence=60, career_path_id=swe_path.id); db.session.add(m6_swe); db.session.flush()
                    s6_1_swe = Step(name="SQL Refresher (Joins, Aggregations, Indices)", sequence=10, estimated_time_minutes=180, milestone_id=m6_swe.id, step_type='Learning')
                    s6_2_swe = Step(name="Using SQLAlchemy with Flask (Models, Sessions, Querying)", sequence=20, estimated_time_minutes=300, milestone_id=m6_swe.id, step_type='Practice')
                    s6_3_swe = Step(name="Database Migrations with Flask-Migrate (Alembic)", sequence=30, estimated_time_minutes=180, milestone_id=m6_swe.id, step_type='Practice')
                    db.session.add_all([s6_1_swe, s6_2_swe, s6_3_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="SQLBolt (Interactive SQL Tutorial)", url="https://sqlbolt.com/", resource_type="Tutorial", step_id=s6_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask-SQLAlchemy Docs", url="https://flask-sqlalchemy.palletsprojects.com/en/3.1.x/", resource_type="Documentation", step_id=s6_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Flask-Migrate Docs", url="https://flask-migrate.readthedocs.io/en/latest/", resource_type="Documentation", step_id=s6_3_swe.id))

                    # Milestone 7: APIs (REST)
                    m7_swe = Milestone(name="APIs (RESTful Principles)", sequence=70, career_path_id=swe_path.id); db.session.add(m7_swe); db.session.flush()
                    s7_1_swe = Step(name="Understanding REST Concepts (Stateless, Resources, Methods, Status Codes)", sequence=10, estimated_time_minutes=120, milestone_id=m7_swe.id, step_type='Learning')
                    s7_2_swe = Step(name="Building a Simple JSON API with Flask", sequence=20, estimated_time_minutes=300, milestone_id=m7_swe.id, step_type='Practice')
                    s7_3_swe = Step(name="API Authentication Basics (Tokens/API Keys)", sequence=30, estimated_time_minutes=120, milestone_id=m7_swe.id, step_type='Learning')
                    s7_4_swe = Step(name="Consuming APIs with Python `requests` library", sequence=40, estimated_time_minutes=120, milestone_id=m7_swe.id, step_type='Practice')
                    db.session.add_all([s7_1_swe, s7_2_swe, s7_3_swe, s7_4_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="What is REST? (Article - Placeholder)", url="#placeholder_swe_article_rest", resource_type="Article", step_id=s7_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Build Flask API (Tutorial - Placeholder)", url="#placeholder_swe_tut_flaskapi", resource_type="Tutorial", step_id=s7_2_swe.id))
                    resources_to_add_swe.append(Resource(name="API Authentication Explained (Blog)", url="#placeholder_swe_blog_apiauth", resource_type="Article", step_id=s7_3_swe.id))
                    resources_to_add_swe.append(Resource(name="Python Requests Library Docs", url="https://requests.readthedocs.io/en/latest/", resource_type="Documentation", step_id=s7_4_swe.id))

                    # Milestone 8: Testing Fundamentals
                    m8_swe = Milestone(name="Testing Fundamentals", sequence=80, career_path_id=swe_path.id); db.session.add(m8_swe); db.session.flush()
                    s8_1_swe = Step(name="Why Test? (Benefits, Types of Tests - Unit/Integration/E2E)", sequence=10, estimated_time_minutes=60, milestone_id=m8_swe.id, step_type='Learning')
                    s8_2_swe = Step(name="Unit Testing Basics with `pytest`", sequence=20, estimated_time_minutes=180, milestone_id=m8_swe.id, step_type='Practice')
                    s8_3_swe = Step(name="Testing Flask Applications (Test Client, Fixtures)", sequence=30, estimated_time_minutes=240, milestone_id=m8_swe.id, step_type='Practice')
                    s8_4_swe = Step(name="Introduction to Mocking", sequence=40, estimated_time_minutes=120, milestone_id=m8_swe.id, step_type='Learning')
                    db.session.add_all([s8_1_swe, s8_2_swe, s8_3_swe, s8_4_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Why Write Tests? (Blog - Placeholder)", url="#placeholder_swe_blog_whytest", resource_type="Article", step_id=s8_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Pytest Introduction (Docs)", url="https://docs.pytest.org/en/stable/", resource_type="Documentation", step_id=s8_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Testing Flask Apps (Flask Docs)", url="https://flask.palletsprojects.com/en/3.0.x/testing/", resource_type="Documentation", step_id=s8_3_swe.id))
                    resources_to_add_swe.append(Resource(name="Python Mocking Intro (Real Python)", url="#placeholder_swe_rp_mocking", resource_type="Article", step_id=s8_4_swe.id))

                    # Milestone 9: Deployment Concepts
                    m9_swe = Milestone(name="Deployment Concepts", sequence=90, career_path_id=swe_path.id); db.session.add(m9_swe); db.session.flush()
                    s9_1_swe = Step(name="Web Servers (Gunicorn) & WSGI", sequence=10, estimated_time_minutes=120, milestone_id=m9_swe.id, step_type='Learning')
                    s9_2_swe = Step(name="Introduction to Docker (Dockerfile, Images, Containers)", sequence=20, estimated_time_minutes=300, milestone_id=m9_swe.id, step_type='Learning')
                    s9_3_swe = Step(name="Basic Docker Compose", sequence=30, estimated_time_minutes=120, milestone_id=m9_swe.id, step_type='Learning')
                    s9_4_swe = Step(name="Cloud Hosting Options Overview (PaaS vs IaaS)", sequence=40, estimated_time_minutes=120, milestone_id=m9_swe.id, step_type='Learning')
                    s9_5_swe = Step(name="Using Environment Variables for Configuration", sequence=50, estimated_time_minutes=60, milestone_id=m9_swe.id, step_type='Practice')
                    db.session.add_all([s9_1_swe, s9_2_swe, s9_3_swe, s9_4_swe, s9_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Gunicorn Docs", url="https://gunicorn.org/#docs", resource_type="Documentation", step_id=s9_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Docker Get Started Guide", url="https://docs.docker.com/get-started/", resource_type="Documentation", step_id=s9_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Docker Compose Overview", url="https://docs.docker.com/compose/", resource_type="Documentation", step_id=s9_3_swe.id))
                    resources_to_add_swe.append(Resource(name="PaaS vs IaaS (Article - Placeholder)", url="#placeholder_swe_article_paas", resource_type="Article", step_id=s9_4_swe.id))
                    resources_to_add_swe.append(Resource(name="Using Environment Variables (Blog - Placeholder)", url="#placeholder_swe_blog_envvars", resource_type="Article", step_id=s9_5_swe.id))

                    # Milestone 10: Building & Showcasing Projects
                    m10_swe = Milestone(name="Building & Showcasing Projects", sequence=100, career_path_id=swe_path.id); db.session.add(m10_swe); db.session.flush()
                    s10_1_swe = Step(name="Plan and Scope a Small Full-Stack Project", sequence=10, estimated_time_minutes=180, milestone_id=m10_swe.id, step_type='Project')
                    s10_2_swe = Step(name="Build Project using Flask, SQLAlchemy, etc.", sequence=20, estimated_time_minutes=1800, milestone_id=m10_swe.id, step_type='Project') # Significant time
                    s10_3_swe = Step(name="Write Basic Tests for Your Project", sequence=30, estimated_time_minutes=300, milestone_id=m10_swe.id, step_type='Project')
                    s10_4_swe = Step(name="Document Your Project (README on GitHub)", sequence=40, estimated_time_minutes=180, milestone_id=m10_swe.id, step_type='Project')
                    s10_5_swe = Step(name="Deploy Your Project (e.g., on Railway)", sequence=50, estimated_time_minutes=240, milestone_id=m10_swe.id, step_type='Project')
                    db.session.add_all([s10_1_swe, s10_2_swe, s10_3_swe, s10_4_swe, s10_5_swe]); db.session.flush()
                    resources_to_add_swe.append(Resource(name="Flask Project Ideas (Blog - Placeholder)", url="#placeholder_swe_blog_ideas", resource_type="Article", step_id=s10_1_swe.id))
                    resources_to_add_swe.append(Resource(name="Example Flask Projects (GitHub Search - Placeholder)", url="#placeholder_swe_github_search", resource_type="Resource", step_id=s10_2_swe.id))
                    resources_to_add_swe.append(Resource(name="Writing Good READMEs (Article - Placeholder)", url="#placeholder_swe_article_readme", resource_type="Article", step_id=s10_4_swe.id))
                    resources_to_add_swe.append(Resource(name="Deploying Flask to Railway (Docs - Placeholder)", url="#placeholder_swe_railway_flask", resource_type="Documentation", step_id=s10_5_swe.id))


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
