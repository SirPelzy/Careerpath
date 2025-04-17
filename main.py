import os
import uuid
import datetime
from datetime import datetime, timedelta # Correct import
from werkzeug.utils import secure_filename
from flask import (Flask, render_template, redirect, url_for, flash, request,
                   abort, current_app, session, send_from_directory, jsonify)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import selectinload
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                       current_user, login_required)
from dotenv import load_dotenv
from flask_migrate import Migrate
from itsdangerous import URLSafeTimedSerializer as Serializer
from itsdangerous.exc import SignatureExpired, BadSignature
import requests
import random
import string
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

# Import Models
from models import (db, User, CareerPath, Milestone, Step, Resource,
                    UserStepStatus, PortfolioItem)

# Import Forms
from forms import (RegistrationForm, LoginForm, OnboardingForm, PortfolioItemForm,
                   EditProfileForm, RecommendationTestForm, ContactForm,
                   VerifyCodeForm, RequestResetForm, ResetPasswordForm)

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# --- Configuration ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a-very-secure-fallback-key-34567_REPLACE_THIS')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
if not app.config['SQLALCHEMY_DATABASE_URI']:
    raise ValueError("No DATABASE_URL set for Flask application")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

# Paystack Config
app.config['PAYSTACK_SECRET_KEY'] = os.environ.get('PAYSTACK_SECRET_KEY')
app.config['PAYSTACK_PUBLIC_KEY'] = os.environ.get('PAYSTACK_PUBLIC_KEY')
if not app.config['PAYSTACK_SECRET_KEY'] or not app.config['PAYSTACK_PUBLIC_KEY']:
     print("WARNING: Paystack API keys not fully configured.")

# Brevo Email API Config
app.config['BREVO_API_KEY'] = os.environ.get('BREVO_API_KEY')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME') # Track Brevo login for check
if not app.config['BREVO_API_KEY'] or not app.config['MAIL_DEFAULT_SENDER'] or not app.config['MAIL_USERNAME']:
    print("WARNING: Brevo API Key, Sender Email or Login Email not configured.")

# Sentry Config
SENTRY_DSN = os.environ.get('SENTRY_DSN')
if SENTRY_DSN:
    try:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[FlaskIntegration()],
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
            environment=os.environ.get('FLASK_ENV', 'development')
        )
        print("Sentry initialized successfully.")
    except Exception as e:
        print(f"ERROR: Failed to initialize Sentry: {e}")
else:
    print("WARNING: SENTRY_DSN environment variable not set. Sentry reporting disabled.")

# --- Initialize Extensions ---
csrf = CSRFProtect(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'
db.init_app(app)
migrate = Migrate(app, db) # Cleaned initialization

# --- Context Processor for Jinja ---
@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()} # Correct usage

# --- User Loader for Flask-Login ---
@login_manager.user_loader
def load_user(user_id):
    """Loads user object for Flask-Login."""
    return User.query.get(int(user_id))

# --- Helper Functions ---
def get_portfolio_upload_path(filename):
    portfolio_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    os.makedirs(portfolio_dir, exist_ok=True)
    return os.path.join(portfolio_dir, filename)

def send_email(to, subject, template_prefix, **kwargs):
    """Sends an email using the Brevo v3 API."""
    api_key = current_app.config.get('BREVO_API_KEY')
    sender_email = current_app.config.get('MAIL_DEFAULT_SENDER')
    sender_name = "Careerpath!"
    if not api_key or not sender_email:
        print("ERROR: Brevo API Key or Sender Email not configured. Cannot send email.")
        return False
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {"accept": "application/json", "api-key": api_key, "content-type": "application/json"}
    try:
        html_content = render_template(template_prefix + '.html', **kwargs)
        text_content = render_template(template_prefix + '.txt', **kwargs)
    except Exception as e_render:
        print(f"ERROR rendering email template {template_prefix}: {e_render}")
        return False
    payload = { "sender": {"email": sender_email, "name": sender_name}, "to": [{"email": to}], "subject": subject, "htmlContent": html_content, "textContent": text_content }
    try:
        print(f"DEBUG: Attempting to send email via Brevo API to {to} with subject '{subject}'")
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 201: print(f"Email sent successfully via Brevo API to {to}. Message ID: {response.json().get('messageId')}"); return True
        else: print(f"ERROR: Brevo API returned status {response.status_code}. Response: {response.text}"); return False
    except requests.exceptions.RequestException as e_req: print(f"ERROR: Network error connecting to Brevo API: {e_req}"); return False
    except Exception as e: print(f"ERROR sending email via Brevo API to {to}: {e}"); return False

# --- Define Plan Details ---
PLANS = {
    'basic':   {'name': 'Basic',   'amount': 8000 * 100, 'plan_code': None},
    'starter': {'name': 'Starter', 'amount': 15000 * 100, 'plan_code': None},
    'pro':     {'name': 'Pro',     'amount': 25000 * 100, 'plan_code': None}
}

# --- Routes ---
@app.route('/')
def home():
    return render_template('home.html', is_homepage=True)

@app.route('/dashboard')
@login_required
def dashboard():
    if not current_user.onboarding_complete:
        flash('Please complete your profile information to get started.', 'info')
        return redirect(url_for('onboarding'))

    target_path = current_user.target_career_path
    milestones = []
    completed_step_ids = set(); milestone_progress = {}; timeline_estimate = "Timeline unavailable"
    total_steps_in_path = 0; total_completed_steps = 0; overall_percent_complete = 0
    recommended_resource_ids = set()

    if target_path:
        milestones = Milestone.query.options(selectinload(Milestone.steps)).filter_by(career_path_id=target_path.id).order_by(Milestone.sequence).all()
        path_steps_resources_query = db.session.query(Step.id, Resource.id, Resource.name, Resource.resource_type).select_from(Step).join(Milestone, Step.milestone_id == Milestone.id).outerjoin(Resource, Step.id == Resource.step_id).filter(Milestone.career_path_id == target_path.id)
        path_steps_resources = path_steps_resources_query.all()
        current_path_step_ids = {step_id for step_id, _, _, _ in path_steps_resources if step_id is not None}
        total_steps_in_path = len(current_path_step_ids)

        if current_path_step_ids:
            completed_statuses_query = UserStepStatus.query.filter(UserStepStatus.user_id == current_user.id, UserStepStatus.status == 'completed', UserStepStatus.step_id.in_(current_path_step_ids)).with_entities(UserStepStatus.step_id)
            completed_step_ids = {step_id for step_id, in completed_statuses_query.all()}
            total_completed_steps = len(completed_step_ids)
            if total_steps_in_path > 0: overall_percent_complete = round((total_completed_steps / total_steps_in_path) * 100)

            for milestone in milestones:
                total_steps_in_milestone = Step.query.filter_by(milestone_id=milestone.id).count()
                if total_steps_in_milestone > 0:
                    milestone_step_ids_query = Step.query.filter_by(milestone_id=milestone.id).with_entities(Step.id)
                    milestone_step_ids = {step_id for step_id, in milestone_step_ids_query.all()}
                    completed_in_milestone = len(completed_step_ids.intersection(milestone_step_ids))
                    percent_complete = round((completed_in_milestone / total_steps_in_milestone) * 100)
                    milestone_progress[milestone.id] = {'completed': completed_in_milestone, 'total': total_steps_in_milestone, 'percent': percent_complete}
                else: milestone_progress[milestone.id] = {'completed': 0, 'total': 0, 'percent': 0}

            if current_user.time_commitment:
                try:
                    commitment_str = current_user.time_commitment; avg_mins_per_week = 0
                    if commitment_str == '<5 hrs': avg_mins_per_week = 2.5 * 60
                    elif commitment_str == '5-10 hrs': avg_mins_per_week = 7.5 * 60
                    elif commitment_str == '10-15 hrs': avg_mins_per_week = 12.5 * 60
                    elif commitment_str == '15+ hrs': avg_mins_per_week = 20 * 60
                    else: avg_mins_per_week = 10 * 60
                    if avg_mins_per_week > 0:
                        remaining_step_ids = current_path_step_ids - completed_step_ids
                        if remaining_step_ids:
                            remaining_steps_data = Step.query.filter(Step.id.in_(remaining_step_ids)).with_entities(Step.estimated_time_minutes).all()
                            total_remaining_minutes = sum(time or 0 for time, in remaining_steps_data)
                            if total_remaining_minutes > 0: estimated_weeks = round(total_remaining_minutes / avg_mins_per_week); timeline_estimate = f"~ {estimated_weeks} weeks remaining (estimated)"
                            else: timeline_estimate = "Remaining steps have no time estimate."
                        else: timeline_estimate = "Congratulations! All steps complete."
                    else: timeline_estimate = "Set weekly time commitment for estimate."
                except Exception as e: print(f"Error calculating timeline: {e}"); timeline_estimate = "Could not calculate timeline."
            else: timeline_estimate = "Set weekly time commitment for estimate."

            user_style = current_user.learning_style; user_interests_str = current_user.interests or ""
            interest_keywords = { keyword.strip().lower() for keyword in user_interests_str.replace(',', ' ').split() if len(keyword.strip()) > 2 }
            style_to_type_map = {'Visual': ['Video', 'Project', 'Course', 'Guide', 'Platform'],'Auditory': ['Video', 'Course'],'Reading/Writing': ['Article', 'Documentation', 'Guide', 'Tutorial', 'Resource'],'Kinesthetic/Practical': ['Project', 'Practice', 'Course', 'Tool', 'Tutorial']}
            preferred_types = style_to_type_map.get(user_style, [])
            for _step_id, resource_id, resource_name, resource_type in path_steps_resources:
                if resource_id is None: continue
                is_recommended = False
                if resource_type and resource_type in preferred_types: is_recommended = True
                if not is_recommended and interest_keywords and resource_name:
                    if any(keyword in resource_name.lower() for keyword in interest_keywords): is_recommended = True
                if is_recommended: recommended_resource_ids.add(resource_id)
        else: timeline_estimate = "No steps defined for this path."

    return render_template('dashboard.html',
                           user=current_user, path=target_path, milestones=milestones,
                           timeline_estimate=timeline_estimate, completed_step_ids=completed_step_ids,
                           milestone_progress=milestone_progress, total_steps_in_path=total_steps_in_path,
                           total_completed_steps=total_completed_steps, overall_percent_complete=overall_percent_complete,
                           recommended_resource_ids=recommended_resource_ids,
                           is_homepage=False, body_class='in-app-layout')

# --- Authentication Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    form = RegistrationForm()
    if form.validate_on_submit():
        existing_user = User.query.filter_by(email=form.email.data.lower()).first()
        if existing_user: flash('That email is already registered. Please log in.', 'warning'); return redirect(url_for('login'))
        try:
            user = User(first_name=form.first_name.data, last_name=form.last_name.data, email=form.email.data.lower())
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            try:
                code = str(random.randint(1000, 9999))
                expiry = datetime.utcnow() + timedelta(minutes=15) # Correct usage
                user.verification_code = code
                user.verification_code_expiry = expiry
                db.session.commit()
                email_sent = send_email(to=user.email, subject='Your Careerpath! Verification Code', template_prefix='email/verify_code', user=user, code=code)
                if email_sent: flash('Account created! Please check your email for the verification code.', 'success')
                else: flash('Account created, but verification code email could not be sent. Please contact support.', 'warning')
                return redirect(url_for('verify_code_entry', email=user.email))
            except Exception as e_code:
                 db.session.rollback(); print(f"Error generating/sending verification code for {user.email}: {e_code}")
                 flash('Account created, but failed to send verification code. Please contact support.', 'warning')
                 return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback(); print(f"Error during registration: {e}")
            flash('An error occurred during registration. Please try again.', 'danger')
    return render_template('register.html', title='Register', form=form, is_homepage=False) # Use dark navbar


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            user.last_login = datetime.utcnow() # Correct usage
            try: db.session.commit()
            except Exception as e: db.session.rollback(); print(f"Error updating last_login: {e}")

            if not user.email_verified:
                try:
                    code = str(random.randint(1000, 9999))
                    expiry = datetime.utcnow() + timedelta(minutes=15) # Correct usage
                    user.verification_code = code
                    user.verification_code_expiry = expiry
                    db.session.commit()
                    email_sent = send_email(to=user.email, subject='Verify Your Email for Careerpath!', template_prefix='email/verify_code', user=user, code=code)
                    if email_sent: flash('Login successful, but please verify your email to continue. A new code has been sent.', 'warning')
                    else: flash('Login successful, but email verification is required and we failed to send a new code. Please contact support.', 'danger')
                except Exception as e_verify:
                    db.session.rollback(); print(f"Error sending verification code during login for {user.email}: {e_verify}")
                    flash('Login successful, but there was an error initiating email verification.', 'danger')
                return redirect(url_for('verify_code_required'))
            else:
                flash('Login Successful!', 'success')
                next_page = request.args.get('next')
                if next_page and not (next_page.startswith('/') or next_page.startswith(request.host_url)): next_page = None
                if not user.onboarding_complete: return redirect(url_for('onboarding'))
                else: return redirect(next_page or url_for('dashboard'))
        else:
            flash('Login Unsuccessful. Please check email and password.', 'danger')
    return render_template('login.html', title='Login', form=form, is_homepage=False) # Use dark navbar

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
    if current_user.onboarding_complete: return redirect(url_for('dashboard'))
    return render_template('onboarding_choice.html', title='Choose Your Start', is_homepage=False, body_class='in-app-layout')

# --- Onboarding Form Route ---
@app.route('/onboarding/form', methods=['GET', 'POST'])
@login_required
def onboarding_form():
    if current_user.onboarding_complete: return redirect(url_for('dashboard'))
    form = OnboardingForm()
    if request.method == 'GET':
        recommended_path_id = request.args.get('recommended_path_id', type=int)
        if recommended_path_id:
            recommended_path = CareerPath.query.get(recommended_path_id)
            if recommended_path: form.target_career_path.data = recommended_path
            else: flash('Invalid recommendation ID provided.', 'warning')
    if form.validate_on_submit():
        try:
            cv_filename_to_save = current_user.cv_filename
            if form.cv_upload.data:
                file = form.cv_upload.data; base_filename = secure_filename(file.filename); unique_id = uuid.uuid4().hex
                name, ext = os.path.splitext(base_filename); ext = ext.lower(); name = name[:100]
                cv_filename_to_save = f"user_{current_user.id}_{unique_id}{ext}"; file_path = os.path.join(app.config['UPLOAD_FOLDER'], cv_filename_to_save)
                if current_user.cv_filename and current_user.cv_filename != cv_filename_to_save:
                   old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.cv_filename)
                   if os.path.exists(old_path):
                       try: os.remove(old_path)
                       except OSError as e: print(f"Error removing old CV {old_path}: {e}")
                file.save(file_path); print(f"CV saved to: {file_path}")
            current_user.target_career_path = form.target_career_path.data; current_user.current_role = form.current_role.data
            current_user.employment_status = form.employment_status.data; current_user.time_commitment = form.time_commitment.data
            current_user.interests = form.interests.data; current_user.learning_style = form.learning_style.data if form.learning_style.data else None
            current_user.cv_filename = cv_filename_to_save; current_user.onboarding_complete = True
            db.session.commit(); flash('Your profile is set up! Welcome to your dashboard.', 'success'); return redirect(url_for('dashboard'))
        except Exception as e:
             db.session.rollback(); print(f"Error during onboarding form save: {e}"); flash('An error occurred while saving your profile.', 'danger')
    return render_template('onboarding_form.html', title='Complete Your Profile', form=form, is_homepage=False, body_class='in-app-layout')

# --- Recommendation Test Route ---
@app.route('/recommendation-test', methods=['GET', 'POST'])
@login_required
def recommendation_test():
    form = RecommendationTestForm()
    if form.validate_on_submit():
        scores = {"Data Analysis / Analytics": 0, "UX/UI Design": 0, "Software Engineering": 0, "Cybersecurity": 0}
        answers = {'q1': form.q1_hobby.data, 'q2': form.q2_approach.data, 'q3': form.q3_reward.data, 'q4': form.q4_feedback.data }
        if answers['q1'] == 'A': scores["Data Analysis / Analytics"] += 1; elif answers['q1'] == 'B': scores["UX/UI Design"] += 1; elif answers['q1'] == 'C': scores["Software Engineering"] += 1; elif answers['q1'] == 'D': scores["Cybersecurity"] += 1
        if answers['q2'] == 'A': scores["Data Analysis / Analytics"] += 1; elif answers['q2'] == 'B': scores["UX/UI Design"] += 1; elif answers['q2'] == 'C': scores["Software Engineering"] += 1; elif answers['q2'] == 'D': scores["Cybersecurity"] += 1
        if answers['q3'] == 'A': scores["Data Analysis / Analytics"] += 1; elif answers['q3'] == 'B': scores["UX/UI Design"] += 1; elif answers['q3'] == 'C': scores["Software Engineering"] += 1; elif answers['q3'] == 'D': scores["Cybersecurity"] += 1
        if answers['q4'] == 'A': scores["Data Analysis / Analytics"] += 1; elif answers['q4'] == 'B': scores["UX/UI Design"] += 1; elif answers['q4'] == 'C': scores["Software Engineering"] += 1; elif answers['q4'] == 'D': scores["Cybersecurity"] += 1
        available_paths = {"Data Analysis / Analytics", "UX/UI Design", "Cybersecurity", "Software Engineering"}
        filtered_scores = {path: score for path, score in scores.items() if path in available_paths and score > 0}
        recommended_paths_info = []
        if not filtered_scores:
            default_path = CareerPath.query.filter_by(name="Data Analysis / Analytics").first()
            if default_path: recommended_paths_info.append({'id': default_path.id, 'name': default_path.name})
            flash("Your answers didn't strongly match a specific path, suggesting Data Analysis as a starting point.", "info")
        else:
            max_score = max(filtered_scores.values()); top_paths_names = [path for path, score in filtered_scores.items() if score == max_score]
            top_paths = CareerPath.query.filter(CareerPath.name.in_(top_paths_names)).order_by(CareerPath.name).all()
            recommended_paths_info = [{'id': p.id, 'name': p.name} for p in top_paths]
            if len(recommended_paths_info) > 1: flash(f"You showed strong interest in multiple areas! Explore the recommendations below.", "info")
            elif not recommended_paths_info: flash("Could not determine recommendation. Please select a path manually.", "warning"); return redirect(url_for('onboarding_form'))
        session['recommendation_scores'] = scores; session['recommended_paths'] = recommended_paths_info
        return redirect(url_for('recommendation_results'))
    return render_template('recommendation_test.html', title="Career Recommendation Test", form=form, is_homepage=False, body_class='in-app-layout')

# --- Recommendation Results Route ---
@app.route('/recommendation-results')
@login_required
def recommendation_results():
    recommended_paths_info = session.pop('recommended_paths', None); scores = session.pop('recommendation_scores', None)
    if not recommended_paths_info: flash('Recommendation results not found or expired.', 'warning'); return redirect(url_for('recommendation_test'))
    is_multiple = len(recommended_paths_info) > 1
    return render_template('recommendation_results.html', title="Your Recommendation", recommended_paths=recommended_paths_info, is_multiple=is_multiple, scores=scores, is_homepage=False, body_class='in-app-layout')

# --- Initial Code Verification Route ---
@app.route('/verify-code', methods=['GET', 'POST'])
def verify_code_entry():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    form = VerifyCodeForm(); email = request.args.get('email')
    if form.validate_on_submit():
        if not email: flash("Could not identify user.", "warning"); return redirect(url_for('login'))
        user = User.query.filter_by(email=email).first(); submitted_code = form.code.data
        if not user: flash("User not found.", "danger"); return redirect(url_for('login'))
        if user.verification_code == submitted_code and user.verification_code_expiry and user.verification_code_expiry > datetime.utcnow(): # Corrected
            try:
                user.email_verified = True; user.verification_code = None; user.verification_code_expiry = None
                db.session.commit(); flash("Email verified successfully! Please log in.", "success"); return redirect(url_for('login'))
            except Exception as e: db.session.rollback(); print(f"Error verifying email for {email}: {e}"); flash("An error occurred.", 'danger')
        else: flash("Invalid or expired verification code.", "danger")
    return render_template('verify_code.html', title="Verify Your Email", form=form, email=email, is_homepage=False) # Dark navbar

# --- Logged-In Code Verification Route ---
@app.route('/verify-code-required', methods=['GET', 'POST'])
@login_required
def verify_code_required():
    if current_user.email_verified: return redirect(url_for('dashboard'))
    form = VerifyCodeForm()
    if form.validate_on_submit():
        submitted_code = form.code.data
        if current_user.verification_code == submitted_code and current_user.verification_code_expiry and current_user.verification_code_expiry > datetime.utcnow(): # Corrected
            try:
                current_user.email_verified = True; current_user.verification_code = None; current_user.verification_code_expiry = None
                db.session.commit(); flash("Email verified successfully! Welcome to your dashboard.", "success")
                return redirect(url_for('dashboard'))
            except Exception as e: db.session.rollback(); print(f"Error verifying email post-login for {current_user.email}: {e}"); flash("An error occurred.", 'danger')
        else: flash("Invalid or expired verification code.", "danger"); return redirect(url_for('verify_code_required'))
    return render_template('verify_code_required.html', title="Verify Email to Continue", form=form, is_homepage=False, body_class='in-app-layout')

# --- Password Reset Routes ---
@app.route("/reset_password", methods=['GET', 'POST'])
def request_reset():
    if current_user.is_authenticated: return redirect(url_for('home'))
    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user:
            try:
                s = Serializer(current_app.config['SECRET_KEY']); token_salt = 'password-reset-salt'
                token = s.dumps(user.id, salt=token_salt); reset_url = url_for('reset_token', token=token, _external=True)
                email_sent = send_email(to=user.email, subject='Password Reset Request - Careerpath!', template_prefix='email/reset_password', user=user, reset_url=reset_url)
                if email_sent: flash('An email has been sent with instructions.', 'info')
                else: flash('Could not send email. Please try again later.', 'danger')
            except Exception as e_token: print(f"Error generating reset token/email for {user.email}: {e_token}"); flash('An error occurred.', 'danger')
        else: flash('If an account exists for that email, instructions have been sent.', 'info')
        return redirect(url_for('login'))
    return render_template('request_reset.html', title='Reset Password Request', form=form, is_homepage=False) # Dark navbar

@app.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_token(token):
    if current_user.is_authenticated: return redirect(url_for('home'))
    user = User.verify_reset_token(token)
    if user is None: flash('Invalid or expired token.', 'warning'); return redirect(url_for('request_reset'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        try: user.set_password(form.password.data); db.session.commit(); flash('Password updated! Please log in.', 'success'); return redirect(url_for('login'))
        except Exception as e: db.session.rollback(); print(f"Error resetting password for user {user.id}: {e}"); flash('An error occurred.', 'danger')
    return render_template('reset_password.html', title='Reset Password', form=form, token=token, is_homepage=False) # Dark navbar

# --- Route to Toggle Step Completion Status (AJAX Version) ---
@app.route('/path/step/<int:step_id>/toggle', methods=['POST'])
@login_required
def toggle_step_status(step_id):
    step = Step.query.get_or_404(step_id); user_status = UserStepStatus.query.filter_by(user_id=current_user.id, step_id=step.id).first()
    new_status = 'not_started'; flash_message = ''; milestone_completed_now = False
    try:
        if user_status:
            if user_status.status == 'completed': user_status.status = 'not_started'; user_status.completed_at = None; new_status = 'not_started'; flash_message = f'Step "{step.name}" marked as not started.'
            else: user_status.status = 'completed'; user_status.completed_at = datetime.utcnow(); new_status = 'completed'; flash_message = f'Step "{step.name}" marked as completed!'
        else: user_status = UserStepStatus(user_id=current_user.id, step_id=step.id, status='completed', completed_at=datetime.utcnow()); db.session.add(user_status); new_status = 'completed'; flash_message = f'Step "{step.name}" marked as completed!'
        db.session.commit()

        updated_milestone_progress = {}; updated_overall_progress = {}
        if step.milestone:
            milestone = step.milestone; m_id = milestone.id; all_m_step_ids_q = Step.query.filter_by(milestone_id=m_id).with_entities(Step.id); all_m_step_ids = {sid for sid, in all_m_step_ids_q.all()}; m_total = len(all_m_step_ids)
            if m_total > 0:
                m_completed_q = UserStepStatus.query.filter(UserStepStatus.user_id == current_user.id, UserStepStatus.status == 'completed', UserStepStatus.step_id.in_(all_m_step_ids)).with_entities(UserStepStatus.step_id)
                m_completed = m_completed_q.count()
                if new_status == 'completed' and m_completed == m_total: milestone_completed_now = True; flash_message += f' Milestone "{milestone.name}" also complete!'
                m_percent = round((m_completed / m_total) * 100); updated_milestone_progress = {'completed': m_completed, 'total': m_total, 'percent': m_percent}
        if current_user.target_career_path:
            all_p_steps_q = Step.query.join(Milestone).filter(Milestone.career_path_id == current_user.target_career_path_id).with_entities(Step.id)
            all_p_step_ids = {sid for sid, in all_p_steps_q.all()}; o_total = len(all_p_step_ids)
            if o_total > 0:
                o_completed_q = UserStepStatus.query.filter(UserStepStatus.user_id == current_user.id, UserStepStatus.status == 'completed', UserStepStatus.step_id.in_(all_p_step_ids))
                o_completed = o_completed_q.count(); o_percent = round((o_completed / o_total) * 100); updated_overall_progress = {'completed': o_completed, 'total': o_total, 'percent': o_percent}

        return jsonify({'success': True, 'new_status': new_status, 'step_id': step.id, 'milestone_id': step.milestone_id,'message': flash_message, 'milestone_completed': milestone_completed_now, 'milestone_progress': updated_milestone_progress, 'overall_progress': updated_overall_progress })
    except Exception as e: db.session.rollback(); print(f"Error updating step status via AJAX for user {current_user.id}, step {step_id}: {e}"); return jsonify({'success': False, 'message': 'An error occurred.'}), 500

# --- Portfolio Routes ---
@app.route('/portfolio')
@login_required
def portfolio():
    items = PortfolioItem.query.filter_by(user_id=current_user.id).order_by(PortfolioItem.created_at.desc()).all()
    return render_template('portfolio.html', title='My Portfolio', portfolio_items=items, is_homepage=False, body_class='in-app-layout')

@app.route('/portfolio/add', methods=['GET', 'POST'])
@login_required
def add_portfolio_item():
    form = PortfolioItemForm(); step_id_from_url = None; milestone_id_from_url = None; linked_item_name = None
    if request.method == 'GET':
        step_id_from_url = request.args.get('step_id', type=int); milestone_id_from_url = request.args.get('milestone_id', type=int)
        if step_id_from_url:
            linked_step = Step.query.get(step_id_from_url)
            if linked_step: linked_item_name = f"Step: {linked_step.name}"
            else: step_id_from_url = None; flash("Invalid associated step ID.", "warning")
    if form.validate_on_submit():
        link_url = form.link_url.data; file_filename_to_save = None
        if form.item_file.data:
            file = form.item_file.data; base_filename = secure_filename(file.filename); unique_id = uuid.uuid4().hex
            name, ext = os.path.splitext(base_filename); ext = ext.lower(); name = name[:100]
            file_filename_to_save = f"user_{current_user.id}_portfolio_{unique_id}{ext}"
            try: file_path = get_portfolio_upload_path(file_filename_to_save); file.save(file_path); print(f"Portfolio file saved to: {file_path}")
            except Exception as e: print(f"Error saving portfolio file: {e}"); flash('Error uploading file.', 'danger'); file_filename_to_save = None
        assoc_step_id = request.form.get('associated_step_id', type=int); assoc_milestone_id = request.form.get('associated_milestone_id', type=int)
        new_item = PortfolioItem(user_id=current_user.id, title=form.title.data, description=form.description.data, item_type=form.item_type.data, link_url=link_url if link_url else None, file_filename=file_filename_to_save, associated_step_id=assoc_step_id, associated_milestone_id=assoc_milestone_id)
        try: db.session.add(new_item); db.session.commit(); flash('Portfolio item added.', 'success'); return redirect(url_for('portfolio'))
        except Exception as e: db.session.rollback(); print(f"Error adding portfolio item to DB: {e}"); flash('Error saving item.', 'danger')
        return render_template('add_edit_portfolio_item.html', title='Add Portfolio Item', form=form, is_edit=False, step_id=assoc_step_id, milestone_id=assoc_milestone_id, linked_item_name=linked_item_name, is_homepage=False, body_class='in-app-layout')
    return render_template('add_edit_portfolio_item.html', title='Add Portfolio Item', form=form, is_edit=False, step_id=step_id_from_url, milestone_id=milestone_id_from_url, linked_item_name=linked_item_name, is_homepage=False, body_class='in-app-layout')

@app.route('/portfolio/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_portfolio_item(item_id):
    item = PortfolioItem.query.get_or_404(item_id)
    if item.user_id != current_user.id: abort(403)
    form = PortfolioItemForm(obj=item); linked_name = None
    if request.method == 'GET':
        if item.associated_step: linked_name = f"Step: {item.associated_step.name}"
        elif item.associated_milestone: linked_name = f"Milestone: {item.associated_milestone.name}"
    if form.validate_on_submit():
        file_filename_to_save = item.file_filename; old_filename_to_delete = None
        if form.item_file.data:
            if item.file_filename: old_filename_to_delete = item.file_filename
            file = form.item_file.data; base_filename = secure_filename(file.filename); unique_id = uuid.uuid4().hex
            name, ext = os.path.splitext(base_filename); ext = ext.lower(); name = name[:100]
            file_filename_to_save = f"user_{current_user.id}_portfolio_{unique_id}{ext}"
            try: file_path = get_portfolio_upload_path(file_filename_to_save); file.save(file_path); print(f"Updated portfolio file saved to: {file_path}")
            except Exception as e: print(f"Error saving updated portfolio file: {e}"); flash('Error uploading new file.', 'danger'); file_filename_to_save = item.file_filename; old_filename_to_delete = None
        item.title = form.title.data; item.description = form.description.data; item.item_type = form.item_type.data
        item.link_url = form.link_url.data if form.link_url.data else None; item.file_filename = file_filename_to_save
        try:
            db.session.commit(); flash('Portfolio item updated.', 'success')
            if old_filename_to_delete:
                try: old_file_path = get_portfolio_upload_path(old_filename_to_delete)
                if os.path.exists(old_file_path): os.remove(old_file_path); print(f"Deleted old portfolio file: {old_file_path}")
                except OSError as e: print(f"Error deleting old portfolio file {old_file_path}: {e}")
            return redirect(url_for('portfolio'))
        except Exception as e: db.session.rollback(); print(f"Error updating portfolio item {item_id}: {e}"); flash('Error updating item.', 'danger')
    return render_template('add_edit_portfolio_item.html', title='Edit Portfolio Item', form=form, is_edit=True, item=item, linked_name=linked_name, is_homepage=False, body_class='in-app-layout')

@app.route('/portfolio/<int:item_id>/delete', methods=['POST'])
@login_required
def delete_portfolio_item(item_id):
    item = PortfolioItem.query.get_or_404(item_id)
    if item.user_id != current_user.id: abort(403)
    filename_to_delete = item.file_filename
    try:
        db.session.delete(item); db.session.commit()
        if filename_to_delete:
            try: file_path = get_portfolio_upload_path(filename_to_delete)
            if os.path.exists(file_path): os.remove(file_path); print(f"Deleted portfolio file: {file_path}")
            except OSError as e: print(f"Error deleting portfolio file {file_path}: {e}")
        flash('Portfolio item deleted.', 'success')
    except Exception as e: db.session.rollback(); print(f"Error deleting portfolio item {item_id}: {e}"); flash('Error deleting item.', 'danger')
    return redirect(url_for('portfolio'))

@app.route('/portfolio/<int:item_id>/unlink', methods=['POST'])
@login_required
def unlink_portfolio_item(item_id):
    item = PortfolioItem.query.get_or_404(item_id)
    if item.user_id != current_user.id: abort(403)
    if item.associated_step_id is None and item.associated_milestone_id is None: flash("This item is not linked.", "info")
    else:
        try: item.associated_step_id = None; item.associated_milestone_id = None; db.session.commit(); flash("Item unlinked.", "success")
        except Exception as e: db.session.rollback(); print(f"Error unlinking portfolio item {item_id}: {e}"); flash("Error unlinking.", "danger")
    return redirect(url_for('edit_portfolio_item', item_id=item.id))

@app.route('/portfolio/download/<int:item_id>')
@login_required
def download_portfolio_file(item_id):
    item = PortfolioItem.query.get_or_404(item_id)
    if item.user_id != current_user.id: abort(403)
    if not item.file_filename: flash("No downloadable file.", "warning"); return redirect(url_for('portfolio'))
    portfolio_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio'); filename = item.file_filename
    try: return send_from_directory(portfolio_dir, filename, as_attachment=True)
    except FileNotFoundError: flash("Error: File not found on server.", "danger"); return redirect(url_for('portfolio'))
    except Exception as e: print(f"Error sending portfolio file {filename}: {e}"); flash("Error downloading file.", "danger"); return redirect(url_for('portfolio'))

# --- CV Download Route ---
@app.route('/cv-download')
@login_required
def download_cv():
    if not current_user.cv_filename: flash("No CV uploaded.", "warning"); return redirect(url_for('profile'))
    cv_directory = app.config['UPLOAD_FOLDER']; filename = current_user.cv_filename
    try: return send_from_directory(cv_directory, filename, as_attachment=True)
    except FileNotFoundError: flash("Error: Your CV file not found.", "danger"); return redirect(url_for('profile'))
    except Exception as e: print(f"Error sending CV file {filename}: {e}"); flash("Error downloading CV.", "danger"); return redirect(url_for('profile'))

# --- CV Delete Route ---
@app.route('/cv-delete', methods=['POST'])
@login_required
def delete_cv():
    filename_to_delete = current_user.cv_filename
    if not filename_to_delete: flash("No CV to delete.", "info"); return redirect(url_for('profile'))
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename_to_delete)
    try:
        if os.path.exists(file_path): os.remove(file_path); print(f"Deleted CV file: {file_path}")
        else: print(f"CV file not found, clearing DB record: {file_path}")
        current_user.cv_filename = None; db.session.commit(); flash("CV deleted.", "success")
    except OSError as e: db.session.rollback(); print(f"Error deleting CV file {file_path}: {e}"); flash("Error deleting CV file.", "danger")
    except Exception as e: db.session.rollback(); print(f"Error clearing CV filename: {e}"); flash("Error updating profile after CV deletion.", "danger")
    return redirect(url_for('profile'))

# --- Pricing Page Route ---
@app.route('/pricing')
def pricing_page():
    return render_template('pricing.html', title='Pricing', is_homepage=True) # Use Public Navbar

# --- Contact Page Route ---
@app.route('/contact', methods=['GET', 'POST'])
def contact_page():
    form = ContactForm()
    if form.validate_on_submit():
        name = form.name.data; email = form.email.data; message = form.message.data
        print(f"Contact Form Submitted:\n Name: {name}\n Email: {email}\n Message: {message}")
        # Add email sending logic here later
        flash("Thank you for your message! We'll get back to you soon.", "success")
        return redirect(url_for('contact_page'))
    return render_template('contact.html', title='Contact Us', form=form, is_homepage=True) # Use Public Navbar


# --- Profile Route ---
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = EditProfileForm(obj=current_user)
    if request.method == 'GET' and current_user.target_career_path: form.target_career_path.data = current_user.target_career_path
    if form.validate_on_submit():
        try:
            cv_filename_to_save = current_user.cv_filename
            if form.cv_upload.data:
                file = form.cv_upload.data; base_filename = secure_filename(file.filename); unique_id = uuid.uuid4().hex
                name, ext = os.path.splitext(base_filename); ext = ext.lower(); name = name[:100]
                cv_filename_to_save = f"user_{current_user.id}_{unique_id}{ext}"; file_path = os.path.join(app.config['UPLOAD_FOLDER'], cv_filename_to_save)
                if current_user.cv_filename and current_user.cv_filename != cv_filename_to_save:
                   old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.cv_filename)
                   if os.path.exists(old_path):
                       try: os.remove(old_path); print(f"Removed old CV during profile update: {old_path}")
                       except OSError as e: print(f"Error removing old CV {old_path}: {e}")
                file.save(file_path); print(f"New CV saved via profile to: {file_path}")
            current_user.first_name = form.first_name.data; current_user.last_name = form.last_name.data
            current_user.target_career_path = form.target_career_path.data; current_user.current_role = form.current_role.data
            current_user.employment_status = form.employment_status.data; current_user.time_commitment = form.time_commitment.data
            current_user.interests = form.interests.data; current_user.learning_style = form.learning_style.data if form.learning_style.data else None
            current_user.cv_filename = cv_filename_to_save; current_user.onboarding_complete = True
            db.session.commit(); flash('Your profile has been updated successfully!', 'success'); return redirect(url_for('profile'))
        except Exception as e: db.session.rollback(); print(f"Error during profile update: {e}"); flash('An error occurred.', 'danger')
    return render_template('profile.html', title='Edit Profile', form=form, is_homepage=False, body_class='in-app-layout')


# --- Main execution ---
if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']): os.makedirs(app.config['UPLOAD_FOLDER'])
    portfolio_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    if not os.path.exists(portfolio_upload_dir): os.makedirs(portfolio_upload_dir)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
