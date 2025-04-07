import os
import uuid
import datetime
from flask_migrate import Migrate
from werkzeug.utils import secure_filename # To sanitize filenames
from flask import Flask, render_template, redirect, url_for, flash, request, abort, current_app, session, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from dotenv import load_dotenv
from sqlalchemy.orm import selectinload
from models import db, User, CareerPath, Milestone, Step, Resource, UserStepStatus, PortfolioItem
from forms import RegistrationForm, LoginForm, OnboardingForm, PortfolioItemForm, EditProfileForm, RecommendationTestForm, ContactForm
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
app.config['PAYSTACK_SECRET_KEY'] = os.environ.get('PAYSTACK_SECRET_KEY')
app.config['PAYSTACK_PUBLIC_KEY'] = os.environ.get('PAYSTACK_PUBLIC_KEY')

if not app.config['PAYSTACK_SECRET_KEY'] or not app.config['PAYSTACK_PUBLIC_KEY']:
     print("WARNING: Paystack API keys not configured.")

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

# --- NEW Email Verification Route ---
@app.route('/verify-email/<token>')
def verify_token(token):
    """Handles email verification via token link."""
    if current_user.is_authenticated and current_user.email_verified:
        flash('Account already verified.', 'info')
        return redirect(url_for('dashboard')) # Already verified and logged in

    user = User.verify_email_token(token) # Use the static method

    if user:
        if user.email_verified:
            flash('Account already verified. Please log in.', 'info')
        else:
            try:
                user.email_verified = True
                db.session.commit()
                flash('Your email has been verified successfully! You can now log in.', 'success')
            except Exception as e:
                 db.session.rollback()
                 print(f"Error marking email verified for user {user.id}: {e}")
                 flash('An error occurred during verification. Please try again or contact support.', 'danger')
                 return redirect(url_for('home')) # Redirect home on error
        return redirect(url_for('login')) # Redirect to login after success or if already verified
    else:
        flash('The email verification link is invalid or has expired.', 'warning')
        # Redirect somewhere sensible, maybe home or register?
        return redirect(url_for('home'))

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
                           is_homepage=False,
                           body_class='in-app-layout') # Pass flag for layout


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
    show_verify_modal = session.pop('show_verify_modal', False) # <-- New Check
    verify_url = session.pop('verify_url', None)
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
    return render_template('login.html', title='Login', form=form, is_homepage=True, show_reset_modal=show_modal, reset_url=reset_url, show_verify_modal=show_verify_modal, verify_url=verify_url)

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
    return render_template('onboarding_choice.html', title='Choose Your Start', is_homepage=False, body_class='in-app-layout')

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
                           is_homepage=False,
                           body_class='in-app-layout')

# --- Recommendation Test Route ---
@app.route('/recommendation-test', methods=['GET', 'POST'])
@login_required
def recommendation_test():
    """Displays and processes the career recommendation test."""
    form = RecommendationTestForm()
    if form.validate_on_submit():
        # --- Scoring Logic (as before) ---
        scores = {"Data Analysis / Analytics": 0, "UX/UI Design": 0, "Software Engineering": 0, "Cybersecurity": 0}
        answers = { 'q1': form.q1_hobby.data, 'q2': form.q2_approach.data, 'q3': form.q3_reward.data, 'q4': form.q4_feedback.data }
        # Q1
        if answers['q1'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q1'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q1'] == 'C': scores["Software Engineering"] += 1
        elif answers['q1'] == 'D': scores["Cybersecurity"] += 1
        # Q2
        if answers['q2'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q2'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q2'] == 'C': scores["Software Engineering"] += 1
        elif answers['q2'] == 'D': scores["Cybersecurity"] += 1
        # Q3
        if answers['q3'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q3'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q3'] == 'C': scores["Software Engineering"] += 1
        elif answers['q3'] == 'D': scores["Cybersecurity"] += 1
        # Q4
        if answers['q4'] == 'A': scores["Data Analysis / Analytics"] += 1
        elif answers['q4'] == 'B': scores["UX/UI Design"] += 1
        elif answers['q4'] == 'C': scores["Software Engineering"] += 1
        elif answers['q4'] == 'D': scores["Cybersecurity"] += 1

        # --- Determine Recommendation(s) & Store in Session ---
        available_paths = {"Data Analysis / Analytics", "UX/UI Design", "Cybersecurity", "Software Engineering"} # Update as paths are fully seeded
        filtered_scores = {path: score for path, score in scores.items() if path in available_paths and score > 0}

        recommended_paths_info = [] # List to store tuples of (id, name)

        if not filtered_scores:
            # Default recommendation if no path scored > 0
            default_path = CareerPath.query.filter_by(name="Data Analysis / Analytics").first()
            if default_path:
                recommended_paths_info.append({'id': default_path.id, 'name': default_path.name})
            flash("Your answers didn't strongly match a specific path, suggesting Data Analysis as a starting point.", "info")
        else:
            max_score = max(filtered_scores.values())
            # Find all paths that have the max score
            top_paths_names = [path for path, score in filtered_scores.items() if score == max_score]

            # Get IDs for the top paths
            top_paths = CareerPath.query.filter(CareerPath.name.in_(top_paths_names)).all()
            recommended_paths_info = [{'id': p.id, 'name': p.name} for p in top_paths]

            if len(recommended_paths_info) > 1:
                 flash(f"You showed strong interest in multiple areas! Explore the recommendations below.", "info")
            elif not recommended_paths_info:
                 # Fallback if names didn't match DB (shouldn't happen)
                 flash("Could not determine recommendation. Please select a path manually.", "warning")
                 return redirect(url_for('onboarding_form'))

        # Store the list of recommended path dictionaries in the session
        session['recommended_paths'] = recommended_paths_info

        return redirect(url_for('recommendation_results'))
        # --- End Recommendation Logic ---

    # Render the test form on GET or if validation fails
    return render_template('recommendation_test.html',
                           title="Career Recommendation Test",
                           form=form,
                           is_homepage=False,
                           body_class='in-app-layout')

# --- Recommendation Results Route ---
@app.route('/recommendation-results') # GET only
@login_required
def recommendation_results():
    """Displays the recommendation results and next steps."""
    # Retrieve list of recommended paths from session
    recommended_paths_info = session.pop('recommended_paths', None) # Use pop to clear after read

    if not recommended_paths_info:
        # If data is missing from session, redirect back to test
        flash('Recommendation results not found or expired. Please try the test again.', 'warning')
        return redirect(url_for('recommendation_test'))

    # Determine if single or multiple recommendations
    is_multiple = len(recommended_paths_info) > 1

    return render_template('recommendation_results.html',
                            title="Your Recommendation",
                            recommended_paths=recommended_paths_info, # Pass the list
                            is_multiple=is_multiple, # Pass flag for template logic
                            is_homepage=False,
                           body_class='in-app-layout')

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
    return render_template('portfolio.html', title='My Portfolio', portfolio_items=items, is_homepage=False, body_class='in-app-layout')

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
                                   is_homepage=False,
                                   body_class='in-app-layout')


    # Render template for GET request or if POST validation failed
    # Pass association IDs from URL (if any) to pre-populate hidden fields on initial GET
    return render_template('add_edit_portfolio_item.html',
                           title='Add Portfolio Item',
                           form=form,
                           is_edit=False,
                           step_id=step_id_from_url, # ID from URL for hidden field
                           milestone_id=milestone_id_from_url, # ID from URL for hidden field
                           linked_item_name=linked_item_name, # Name from URL for display
                           is_homepage=False,
                           body_class='in-app-layout')

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

    return render_template('add_edit_portfolio_item.html', title='Edit Portfolio Item', form=form, is_edit=True, item=item, is_homepage=False, body_class='in-app-layout')


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

# --- NEW Portfolio File Download Route ---
@app.route('/portfolio/download/<int:item_id>')
@login_required
def download_portfolio_file(item_id):
    """Provides download access to an uploaded portfolio file, checking ownership."""
    item = PortfolioItem.query.get_or_404(item_id)

    # 1. Check Ownership
    if item.user_id != current_user.id:
        abort(403) # Forbidden

    # 2. Check if a file is actually associated
    if not item.file_filename:
        flash("No downloadable file associated with this portfolio item.", "warning")
        return redirect(url_for('portfolio')) # Or maybe item detail page if you create one

    # 3. Construct the path to the file
    # Ensure this path construction matches where files are SAVED
    # (Uses the get_portfolio_upload_path helper's logic implicitly)
    portfolio_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    filename = item.file_filename

    # 4. Use send_from_directory for security (prevents path traversal)
    try:
        return send_from_directory(portfolio_dir, filename, as_attachment=True)
        # as_attachment=True prompts browser to download instead of displaying
    except FileNotFoundError:
        abort(404) # File record exists but file is missing on server
    except Exception as e:
        print(f"Error sending portfolio file {filename}: {e}")
        abort(500)

# --- NEW Pricing Page Route ---
@app.route('/pricing')
def pricing_page():
    """Displays the pricing page."""
    # Use is_homepage=True to show the main public navbar
    return render_template('pricing.html', title='Pricing', is_homepage=True)

# --- NEW Contact Page Route ---
@app.route('/contact', methods=['GET', 'POST'])
def contact_page():
    """Displays and handles the contact form."""
    form = ContactForm()
    if form.validate_on_submit():
        # Process the form data (e.g., send email)
        name = form.name.data
        email = form.email.data
        message = form.message.data

        # --- Placeholder for Email Sending Logic ---
        print(f"Contact Form Submitted:\n Name: {name}\n Email: {email}\n Message: {message}")
        # In production, you would integrate Flask-Mail here to send the email
        # mail.send_message(...)
        # --- End Placeholder ---

        flash("Thank you for your message! We'll get back to you soon.", "success")
        return redirect(url_for('contact_page')) # Redirect back to clear form

    # Assume contact uses homepage style (main nav)
    return render_template('contact.html', title='Contact Us', form=form, is_homepage=True)

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
                           is_homepage=False,
                           body_class='in-app-layout') # Use sidebar navigation


# --- Route to Toggle Step Completion Status (AJAX Version) ---
@app.route('/path/step/<int:step_id>/toggle', methods=['POST'])
@login_required
def toggle_step_status(step_id):
    """Handles AJAX request to mark a step as complete or incomplete."""
    step = Step.query.get_or_404(step_id)

    # Optional ownership check (if needed)
    # if step.milestone.career_path_id != current_user.target_career_path_id:
    #     return jsonify({'success': False, 'message': 'Unauthorized action.'}), 403

    user_status = UserStepStatus.query.filter_by(user_id=current_user.id, step_id=step.id).first()
    new_status = 'not_started' # Default assumption
    flash_message = ''

    try:
        if user_status:
            if user_status.status == 'completed':
                user_status.status = 'not_started'
                user_status.completed_at = None
                new_status = 'not_started'
                flash_message = f'Step "{step.name}" marked as not started.'
            else:
                user_status.status = 'completed'
                user_status.completed_at = datetime.datetime.utcnow()
                new_status = 'completed'
                flash_message = f'Step "{step.name}" marked as completed!'
        else:
            user_status = UserStepStatus(
                user_id=current_user.id,
                step_id=step.id,
                status='completed',
                completed_at=datetime.datetime.utcnow()
            )
            db.session.add(user_status)
            new_status = 'completed'
            flash_message = f'Step "{step.name}" marked as completed!'

        db.session.commit()

        # --- Check for milestone completion AFTER commit ---
        milestone_completed_now = False
        if new_status == 'completed' and step.milestone:
             milestone = step.milestone
             all_milestone_step_ids_query = Step.query.filter_by(milestone_id=milestone.id).with_entities(Step.id)
             all_milestone_step_ids = {step_id for step_id, in all_milestone_step_ids_query.all()}
             total_steps_in_milestone = len(all_milestone_step_ids)
             if total_steps_in_milestone > 0:
                 completed_statuses_query = UserStepStatus.query.filter(
                     UserStepStatus.user_id == current_user.id,
                     UserStepStatus.status == 'completed',
                     UserStepStatus.step_id.in_(all_milestone_step_ids)
                 ).with_entities(UserStepStatus.step_id)
                 completed_count = completed_statuses_query.count() # Use count() query
                 if completed_count == total_steps_in_milestone:
                     milestone_completed_now = True
                     flash_message += f' Milestone "{milestone.name}" also complete!' # Append to message

        # Return JSON instead of redirecting
        return jsonify({
            'success': True,
            'new_status': new_status,
            'step_id': step.id,
            'milestone_id': step.milestone_id, # Send milestone ID
            'message': flash_message,
            'milestone_completed': milestone_completed_now # Send flag
        })

    except Exception as e:
        db.session.rollback()
        print(f"Error updating step status via AJAX for user {current_user.id}, step {step_id}: {e}")
        # Return JSON error
        return jsonify({'success': False, 'message': 'An error occurred while updating status.'}), 500

# --- NEW CV Download Route ---
@app.route('/cv-download')
@login_required
def download_cv():
    """Allows the user to download their uploaded CV."""
    if not current_user.cv_filename:
        flash("No CV uploaded.", "warning")
        return redirect(url_for('profile'))

    # CVs are stored directly in UPLOAD_FOLDER based on current logic
    cv_directory = app.config['UPLOAD_FOLDER']
    filename = current_user.cv_filename

    try:
        return send_from_directory(cv_directory, filename, as_attachment=True)
    except FileNotFoundError:
        # Optionally clear the bad filename from DB if file missing? Or just show error.
        # current_user.cv_filename = None
        # db.session.commit()
        flash("Error: Your CV file was not found on the server. Please upload it again.", "danger")
        return redirect(url_for('profile'))
    except Exception as e:
        print(f"Error sending CV file {filename} for user {current_user.id}: {e}")
        flash("An error occurred while trying to download your CV.", "danger")
        return redirect(url_for('profile'))


# --- NEW CV Delete Route ---
@app.route('/cv-delete', methods=['POST'])
@login_required
def delete_cv():
    """Deletes the user's uploaded CV."""
    filename_to_delete = current_user.cv_filename

    if not filename_to_delete:
        flash("No CV to delete.", "info")
        return redirect(url_for('profile'))

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename_to_delete)

    try:
        # Delete file from filesystem first
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Deleted CV file: {file_path}")
        else:
            print(f"CV file not found for deletion, but clearing DB record: {file_path}")

        # Clear filename reference in database
        current_user.cv_filename = None
        db.session.commit()
        flash("CV deleted successfully.", "success")

    except OSError as e:
        # Error during file deletion
        db.session.rollback() # Rollback DB change if file deletion failed critically
        print(f"Error deleting CV file {file_path}: {e}")
        flash("An error occurred while deleting the CV file.", "danger")
    except Exception as e:
        # Error during DB commit
        db.session.rollback()
        print(f"Error clearing CV filename in DB for user {current_user.id}: {e}")
        flash("An error occurred while updating your profile after CV deletion.", "danger")

    return redirect(url_for('profile'))

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
