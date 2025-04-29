import os
import uuid
from datetime import datetime, timedelta
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from flask import Flask, render_template, redirect, url_for, flash, request, abort, current_app, session, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from dotenv import load_dotenv
from sqlalchemy.orm import selectinload
from models import db, User, CareerPath, Milestone, Step, Resource, UserStepStatus, PortfolioItem
from forms import RegistrationForm, LoginForm, OnboardingForm, PortfolioItemForm, EditProfileForm, RecommendationTestForm, ContactForm, VerifyCodeForm
from forms import RequestResetForm, ResetPasswordForm
from itsdangerous import URLSafeTimedSerializer as Serializer
import requests
import random
import string
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from functools import wraps
from forms import CVHelperForm
import re
from flask_dance.contrib.google import make_google_blueprint
from flask_dance.consumer import oauth_authorized
from werkzeug.security import generate_password_hash
import secrets
from werkzeug.middleware.proxy_fix import ProxyFix

# --- NEW Email Sending Helper (using Brevo API) ---
def send_email(to, subject, template_prefix, **kwargs):
    """Sends an email using the Brevo v3 API."""
    api_key = current_app.config.get('BREVO_API_KEY')
    sender_email = current_app.config.get('MAIL_DEFAULT_SENDER')
    sender_name = "Careerpath!"

    if not api_key or not sender_email:
        print("ERROR: Brevo API Key or Sender Email not configured. Cannot send email.")
        return False

    # Brevo API v3 endpoint for sending transactional emails
    url = "https://api.brevo.com/v3/smtp/email"

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json"
    }

    # Render email bodies using existing templates
    try:
        html_content = render_template(template_prefix + '.html', **kwargs)
        text_content = render_template(template_prefix + '.txt', **kwargs)
    except Exception as e_render:
        print(f"ERROR rendering email template {template_prefix}: {e_render}")
        return False

    # Construct the payload according to Brevo API v3 documentation
    payload = {
        "sender": {"email": sender_email, "name": sender_name},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html_content,
        "textContent": text_content
    }

    try:
        print(f"DEBUG: Attempting to send email via Brevo API to {to} with subject '{subject}'")
        response = requests.post(url, headers=headers, json=payload, timeout=20)

        # Check Brevo API response
        if response.status_code == 201:
            print(f"Email sent successfully via Brevo API to {to}. Message ID: {response.json().get('messageId')}")
            return True
        else:
            print(f"ERROR: Brevo API returned status {response.status_code}. Response: {response.text}")
            return False

    except requests.exceptions.RequestException as e_req:
        print(f"ERROR: Network error connecting to Brevo API: {e_req}")
        return False
    except Exception as e:
        print(f"ERROR sending email via Brevo API to {to}: {e}")
        return False


def plan_required(*allowed_plans):
    """
    Decorator to restrict access to routes based on the user's subscription plan.
    Checks for authentication first.
    Assumes user.plan stores the plan name (e.g., 'Free', 'Basic', 'Starter', 'Pro').
    Plan names are checked case-insensitively.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 1. Check if user is logged in (handled by @login_required typically, but good practice)
            if not current_user.is_authenticated:
                # Use Flask-Login's mechanism to handle unauthorized access
                return login_manager.unauthorized()

            # 2. Check if the user's plan is allowed
            user_plan = getattr(current_user, 'plan', 'Free').lower() # Default to 'Free' if no plan attribute
            normalized_allowed_plans = {plan.lower() for plan in allowed_plans}

            if user_plan not in normalized_allowed_plans:
                # 3. If plan not allowed, flash message and redirect
                feature_name = f.__name__.replace('_', ' ').title() # Get a readable function name
                flash(f'Access to the "{feature_name}" feature requires an upgraded plan. Please select a suitable plan below.', 'warning')
                return redirect(url_for('pricing_page')) # Redirect to pricing

            # 4. If plan is allowed, execute the original route function
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- End Decorator Definition ---

COMMON_TECH_KEYWORDS = set([
    # Programming Languages
    'python', 'javascript', 'java', 'c#', 'c++', 'php', 'ruby', 'go', 'swift', 'kotlin', 'typescript', 'sql',
    # Frontend Frameworks/Libs
    'react', 'angular', 'vue', 'svelte', 'jquery', 'html', 'css', 'bootstrap', 'tailwind', 'sass', 'less',
    # Backend Frameworks/Libs
    'node.js', 'express', 'django', 'flask', 'ruby on rails', 'spring boot', '.net', 'laravel',
    # Databases
    'postgresql', 'mysql', 'sqlite', 'mongodb', 'redis', 'sql server', 'oracle', 'nosql',
    # Cloud / DevOps
    'aws', 'azure', 'gcp', 'docker', 'kubernetes', 'terraform', 'ansible', 'jenkins', 'git', 'github', 'gitlab', 'ci/cd',
    # Data Science / ML
    'pandas', 'numpy', 'scipy', 'scikit-learn', 'tensorflow', 'pytorch', 'keras', 'matplotlib', 'seaborn', 'power bi', 'tableau', 'excel', 'machine learning', 'data analysis', 'statistics',
    # UX/UI
    'figma', 'sketch', 'adobe xd', 'invision', 'user research', 'wireframing', 'prototyping', 'user testing', 'design system', 'ui', 'ux', 'user interface', 'user experience',
    # Cybersecurity
    'security+', 'ceh', 'cissp', 'nmap', 'wireshark', 'metasploit', 'siem', 'ids/ips', 'firewall', 'vpn', 'penetration testing', 'vulnerability assessment', 'incident response', 'owasp', 'nist', 'iso 27001',
    # Soft Skills / Other
    'agile', 'scrum', 'jira', 'communication', 'teamwork', 'leadership', 'problem solving', 'management', 'analysis', 'design', 'collaboration'
])


INTERVIEW_QUESTIONS = {
    'General': [
        "Tell me about yourself.",
        "Why are you interested in this role/company?",
        "What are your strengths?",
        "What are your weaknesses?",
        "Describe a challenging project you worked on and how you handled it (STAR method).",
        "Describe a time you failed and what you learned.",
        "Where do you see yourself in 5 years?",
        "Why do you want to transition into tech / this specific field?",
        "How do you handle working under pressure or tight deadlines?",
        "Do you have any questions for us?"
    ],
    'Data Analysis / Analytics': [
        "Explain the difference between SQL JOIN types (INNER, LEFT, RIGHT, FULL OUTER).",
        "What is a primary key and a foreign key?",
        "How would you handle missing data in a dataset?",
        "Describe different types of data visualizations and when to use them.",
        "Explain selection bias.",
        "What are aggregate functions in SQL? Give examples.",
        "Describe a data analysis project you completed (mention tools used, process, outcome).",
        "How would you explain p-value to a non-technical person?",
        "Scenario: How would you investigate a sudden drop in user engagement metrics?",
        "Python: How do you group data using Pandas?" # Example technical
    ],
    'UX/UI Design': [
        "Walk me through your design process.",
        "Tell me about a project in your portfolio you're proud of and why.",
        "How do you handle negative feedback on your designs?",
        "What's the difference between UX and UI?",
        "How do you conduct user research?",
        "Explain responsive design.",
        "What are usability heuristics?",
        "Describe your experience with Figma (or other relevant tool).",
        "How do you ensure your designs are accessible?",
        "Scenario: How would you redesign the login flow for this app?"
    ],
    'Cybersecurity': [
        "Explain the CIA triad.",
        "What is the difference between symmetric and asymmetric encryption?",
        "Describe common types of malware.",
        "What is the purpose of a firewall?",
        "Explain the difference between vulnerability assessment and penetration testing.",
        "What steps would you take if you suspected a system was compromised?",
        "What is social engineering? Give examples.",
        "Explain the concept of least privilege.",
        "What is OWASP Top 10?",
        "Describe your familiarity with Linux command line."
    ],
    'Software Engineering': [
        "Explain Object-Oriented Programming (OOP) principles.",
        "What is the difference between a list and a tuple in Python?",
        "Describe the request/response cycle in web applications.",
        "What is version control and why is it important? Describe a Git workflow.",
        "Explain RESTful APIs.",
        "What are common data structures? When would you use a dictionary vs a list?",
        "Describe unit testing.",
        "What is the difference between SQL and NoSQL databases?",
        "Explain the concept of dependency injection.",
        "Scenario: How would you approach debugging a slow API endpoint?"
    ]

}

# --- Define Plan Details ---
# Prices are in kobo (lowest currency unit for NGN)
PLANS = {
    'basic': {'name': 'Basic', 'amount': 8000 * 100, 'plan_code': None},
    'starter': {'name': 'Starter', 'amount': 15000 * 100, 'plan_code': None},
    'pro': {'name': 'Pro', 'amount': 25000 * 100, 'plan_code': None}
}

print("DEBUG: Importing Migrate...")
try:
    from flask_migrate import Migrate
    print("DEBUG: Imported Migrate successfully.")
except ImportError as e:
    print(f"DEBUG: FAILED to import Migrate: {e}")
    Migrate = None

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# --- Configuration ---

app.config['GCS_BUCKET_NAME'] = os.environ.get('GCS_BUCKET_NAME')
if not app.config['GCS_BUCKET_NAME']:
    print("WARNING: GCS_BUCKET_NAME not configured.")
    

app.config['GOOGLE_OAUTH_CLIENT_ID'] = os.environ.get('GOOGLE_OAUTH_CLIENT_ID')
app.config['GOOGLE_OAUTH_CLIENT_SECRET'] = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')
# For local testing over HTTP if needed (set in .env):
if os.environ.get('OAUTHLIB_INSECURE_TRANSPORT'):
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

if not app.config['GOOGLE_OAUTH_CLIENT_ID'] or not app.config['GOOGLE_OAUTH_CLIENT_SECRET']:
     print("WARNING: Google OAuth credentials not fully configured.")

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a-very-secure-fallback-key-34567')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
if not app.config['SQLALCHEMY_DATABASE_URI']:
    raise ValueError("No DATABASE_URL set for Flask application")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
app.config['PAYSTACK_SECRET_KEY'] = os.environ.get('PAYSTACK_SECRET_KEY')
app.config['PAYSTACK_PUBLIC_KEY'] = os.environ.get('PAYSTACK_PUBLIC_KEY')

if not app.config['PAYSTACK_SECRET_KEY'] or not app.config['PAYSTACK_PUBLIC_KEY']:
    print("WARNING: Paystack API keys not configured.")

app.config['BREVO_API_KEY'] = os.environ.get('BREVO_API_KEY')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')

if not app.config['BREVO_API_KEY'] or not app.config['MAIL_DEFAULT_SENDER']:
    print("WARNING: Brevo API Key or Mail Sender not configured.")

# --- Initialize Extensions ---
csrf = CSRFProtect(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'
db.init_app(app)

migrate = Migrate(app, db)

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

# Initialize Migrate only if import succeeded
migrate = None
if Migrate:
    print("DEBUG: Attempting to initialize Migrate...")
    try:
        migrate = Migrate(app, db)
        print("DEBUG: Initialized Migrate successfully.")
    except Exception as e:
        print(f"DEBUG: ERROR initializing Migrate: {e}")
        migrate = None
else:
    print("DEBUG: Skipping Migrate initialization due to import failure.")

# --- Context Processor for Jinja ---
@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}

# --- User Loader for Flask-Login ---
@login_manager.user_loader
def load_user(user_id):
    """Loads user object for Flask-Login."""
    return User.query.get(int(user_id))


# Create Google OAuth Blueprint using loaded config
google_bp = make_google_blueprint(
    client_id=app.config.get('GOOGLE_OAUTH_CLIENT_ID'),
    client_secret=app.config.get('GOOGLE_OAUTH_CLIENT_SECRET'),
    scope=["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"],
    # redirect_to="google_auth_callback" # Optional: specify explicit callback route
)
app.register_blueprint(google_bp, url_prefix="/login")

# --- Routes ---
@app.route('/')
def home():
    return render_template('home.html', is_homepage=True)



# --- Google OAuth Callback/Signal Handler ---
@oauth_authorized.connect_via(google_bp)
def google_logged_in(blueprint, token):
    """Handles user login/registration after successful Google OAuth."""
    if not token:
        flash("Failed to log in with Google.", category="danger")
        return redirect(url_for("login")) # Redirect to login page on failure

    # Fetch user info from Google
    # blueprint.session is an OAuth2Session instance provided by Flask-Dance
    resp = blueprint.session.get("/oauth2/v3/userinfo")
    if not resp.ok:
        msg = "Failed to fetch user information from Google."
        print(f"OAuth Error: {msg} Status: {resp.status_code} Response: {resp.text}")
        flash(msg, category="danger")
        return redirect(url_for("login"))

    user_info = resp.json()
    user_email = user_info.get("email", "").lower()
    user_google_id = user_info.get("sub") # Optional: Store this later

    if not user_email:
        flash("Could not get email address from Google.", category="warning")
        return redirect(url_for("login"))

    # Find or create the user in our database
    user = User.query.filter_by(email=user_email).first()

    if not user:
        # Create a new user
        new_user = User(
            email=user_email,
            first_name=user_info.get("given_name", ""),
            last_name=user_info.get("family_name", ""),
            password_hash=generate_password_hash(secrets.token_urlsafe(32)), # Unusable password
            email_verified=user_info.get("email_verified", False),
            onboarding_complete=False # Require onboarding
        )
        try:
            db.session.add(new_user)
            db.session.commit()
            user = new_user
            flash("Account created via Google! Please complete your profile.", "success")
            print(f"New user created via Google: {user.email}")
        except Exception as e:
            db.session.rollback()
            print(f"Error creating OAuth user {user_email}: {e}")
            flash("Error creating your account via Google. Try manual registration.", "danger")
            return redirect(url_for("register")) # Redirect to manual register
    else:
        # User exists - update verification status if needed
        if not user.email_verified and user_info.get("email_verified", False):
            try:
                user.email_verified = True
                db.session.commit()
                print(f"Marked existing user {user.email} as verified via Google OAuth.")
            except Exception as e:
                 db.session.rollback()
                 print(f"Error updating email verification for {user.email} during OAuth: {e}")
        flash(f"Welcome back, {user.first_name}!", "success")


    # Log the user in using Flask-Login
    try:
        # 'remember=True' keeps user logged in longer
        login_user(user, remember=True)
        session.pop('_flashes', None) # Clear Flask-Dance flashes if any
    except Exception as e:
         print(f"Error logging in user {user.email} after OAuth: {e}")
         flash("Logged in with Google, but couldn't start session. Please try again.", "danger")
         return redirect(url_for("login"))

    # Determine redirect destination
    if not user.onboarding_complete:
        return redirect(url_for('onboarding'))
    else:
        # Redirect directly to dashboard after OAuth login
        return redirect(url_for('dashboard'))

    # Normally return False tells Flask-Dance we handled it,
    # but explicit redirects above are clearer.
    # return False


# --- NEW Subscription Initiation Route ---
@app.route('/subscribe/<plan_name>')
@login_required
def subscribe(plan_name):
    """Initiates a Paystack transaction for a selected plan."""
    plan = PLANS.get(plan_name.lower())
    secret_key = current_app.config.get('PAYSTACK_SECRET_KEY')

    if not plan:
        flash("Invalid pricing plan selected.", "danger")
        return redirect(url_for('pricing_page'))

    if not secret_key:
        flash("Payment gateway not configured. Please contact support.", "danger")
        return redirect(url_for('pricing_page'))

    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    reference = f"CPTH_{current_user.id}_{timestamp}_{random_str}"

    url = "https://api.paystack.co/transaction/initialize"

    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "email": current_user.email,
        "amount": plan['amount'],
        "reference": reference,
        "callback_url": url_for('payment_callback', _external=True),
        "metadata": {
            "user_id": current_user.id,
            "plan_name": plan['name'],
            "custom_fields": [
                {"display_name": "User Name", "variable_name": "user_name", "value": f"{current_user.first_name} {current_user.last_name}"}
            ]
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        response_data = response.json()

        if response_data.get("status") and response_data.get("data") and response_data["data"].get("authorization_url"):
            auth_url = response_data["data"]["authorization_url"]
            print(f"Redirecting user {current_user.id} to Paystack: {auth_url}")
            return redirect(auth_url)
        else:
            print(f"Paystack init error response: {response_data}")
            flash(f"Could not initiate payment: {response_data.get('message', 'Unknown error')}", "danger")
            return redirect(url_for('pricing_page'))

    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Paystack: {e}")
        flash("Could not connect to payment gateway. Please try again later.", "danger")
        return redirect(url_for('pricing_page'))
    except Exception as e:
        print(f"Error during payment initiation: {e}")
        flash("An unexpected error occurred during payment initiation.", "danger")
        return redirect(url_for('pricing_page'))

# --- NEW Payment Callback Route ---
@app.route('/payment/callback')
def payment_callback():
    """Handles the redirect back from Paystack after payment attempt."""
    reference = request.args.get('reference')
    secret_key = current_app.config.get('PAYSTACK_SECRET_KEY')

    if not reference:
        flash("Payment reference missing.", "warning")
        return redirect(url_for('pricing_page'))

    if not secret_key:
        flash("Payment gateway configuration error.", "danger")
        return redirect(url_for('home'))

    url = f"https://api.paystack.co/transaction/verify/{reference}"
    headers = {"Authorization": f"Bearer {secret_key}"}

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        response_data = response.json()

        if response_data.get("status"):
            data = response_data["data"]
            if data.get("status") == "success":
                paid_amount = data.get("amount")
                customer_email = data.get("customer", {}).get("email", "").lower()
                metadata_plan = data.get("metadata", {}).get("plan_name")

                user = User.query.filter_by(email=customer_email).first()
                if not user:
                    print(f"Verification successful but user not found for email: {customer_email}")
                    flash("Payment verified, but could not find associated user account.", "warning")
                    return redirect(url_for('login'))

                plan = PLANS.get(metadata_plan.lower() if metadata_plan else None)
                if not plan or paid_amount != plan['amount']:
                    print(f"Verification successful but amount mismatch for ref {reference}. Paid: {paid_amount}, Expected: {plan['amount'] if plan else 'N/A'}")
                    flash("Payment verified, but amount did not match expected plan price. Please contact support.", "danger")
                    if login_user(user):
                        return redirect(url_for('profile'))
                    else:
                        return redirect(url_for('login'))

                print(f"Updating plan for user {user.id} to {plan['name']}")
                user.plan = plan['name']
                user.subscription_active = True
                user.subscription_expiry = None

                try:
                    db.session.commit()
                    flash(f"Payment successful! Your account has been upgraded to the {plan['name']} plan.", "success")
                    if not current_user.is_authenticated:
                        login_user(user)
                    return redirect(url_for('dashboard'))
                except Exception as e_db:
                    db.session.rollback()
                    print(f"DB Error updating user plan after successful payment {reference}: {e_db}")
                    flash("Payment successful, but failed to update your account plan. Please contact support.", "danger")
                    if login_user(user):
                        return redirect(url_for('profile'))
                    else:
                        return redirect(url_for('login'))

            else:
                print(f"Paystack verification status not 'success' for ref {reference}: {data.get('status')}")
                flash(f"Payment was not successful ({data.get('gateway_response', 'No details')}). Please try again.", "warning")
                return redirect(url_for('pricing_page'))
        else:
            print(f"Paystack verify error response: {response_data}")
            flash(f"Could not verify payment: {response_data.get('message', 'Unknown error')}", "danger")
            return redirect(url_for('pricing_page'))

    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Paystack for verification: {e}")
        flash("Could not connect to payment gateway to verify payment. Please contact support if payment was made.", "danger")
        return redirect(url_for('pricing_page'))
    except Exception as e:
        print(f"Error during payment callback processing: {e}")
        flash("An unexpected error occurred during payment verification.", "danger")
        return redirect(url_for('pricing_page'))

# --- NEW Email Verification Route ---
@app.route('/verify-email/<token>')
def verify_token(token):
    """Handles email verification via token link."""
    if current_user.is_authenticated and current_user.email_verified:
        flash('Account already verified.', 'info')
        return redirect(url_for('dashboard'))

    user = User.verify_email_token(token)

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
                return redirect(url_for('home'))
        return redirect(url_for('login'))
    else:
        flash('The email verification link is invalid or has expired.', 'warning')
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
    total_steps_in_path = 0
    total_completed_steps = 0
    overall_percent_complete = 0
    recommended_resource_ids = set()

    if target_path:
        milestones = Milestone.query.options(
            selectinload(Milestone.steps)
        ).filter_by(
            career_path_id=target_path.id
        ).order_by(Milestone.sequence).all()

        path_steps_resources_query = db.session.query(
            Step.id,
            Resource.id,
            Resource.name,
            Resource.resource_type
        ).select_from(Step).join(
            Milestone, Step.milestone_id == Milestone.id
        ).outerjoin(
            Resource, Step.id == Resource.step_id
        ).filter(
            Milestone.career_path_id == target_path.id
        )
        path_steps_resources = path_steps_resources_query.all()

        current_path_step_ids = {step_id for step_id, _, _, _ in path_steps_resources if step_id is not None}
        total_steps_in_path = len(current_path_step_ids)

        if current_path_step_ids:
            completed_statuses_query = UserStepStatus.query.filter(
                UserStepStatus.user_id == current_user.id,
                UserStepStatus.status == 'completed',
                UserStepStatus.step_id.in_(current_path_step_ids)
            ).with_entities(UserStepStatus.step_id)
            completed_step_ids = {step_id for step_id, in completed_statuses_query.all()}
            total_completed_steps = len(completed_step_ids)

            if total_steps_in_path > 0:
                overall_percent_complete = round((total_completed_steps / total_steps_in_path) * 100)

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

            if current_user.time_commitment:
                try:
                    commitment_str = current_user.time_commitment
                    avg_mins_per_week = 0
                    if commitment_str == '<5 hrs':
                        avg_mins_per_week = 2.5 * 60
                    elif commitment_str == '5-10 hrs':
                        avg_mins_per_week = 7.5 * 60
                    elif commitment_str == '10-15 hrs':
                        avg_mins_per_week = 12.5 * 60
                    elif commitment_str == '15+ hrs':
                        avg_mins_per_week = 20 * 60
                    else:
                        avg_mins_per_week = 10 * 60

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
                        else:
                            timeline_estimate = "Congratulations! All steps complete."
                    else:
                        timeline_estimate = "Set weekly time commitment for estimate."
                except Exception as e:
                    print(f"Error calculating timeline: {e}")
                    timeline_estimate = "Could not calculate timeline."
            else:
                timeline_estimate = "Set weekly time commitment for estimate."

            user_style = current_user.learning_style
            user_interests_str = current_user.interests or ""
            interest_keywords = {
                keyword.strip().lower()
                for keyword in user_interests_str.replace(',', ' ').split()
                if len(keyword.strip()) > 2
            }

            style_to_type_map = {
                'Visual': ['Video', 'Project', 'Course', 'Guide', 'Platform'],
                'Auditory': ['Video', 'Course'],
                'Reading/Writing': ['Article', 'Documentation', 'Guide', 'Tutorial', 'Resource'],
                'Kinesthetic/Practical': ['Project', 'Practice', 'Course', 'Tool', 'Tutorial']
            }
            preferred_types = style_to_type_map.get(user_style, [])

            for _step_id, resource_id, resource_name, resource_type in path_steps_resources:
                if resource_id is None:
                    continue

                is_recommended = False
                if resource_type and resource_type in preferred_types:
                    is_recommended = True

                if not is_recommended and interest_keywords and resource_name:
                    resource_name_lower = resource_name.lower()
                    if any(keyword in resource_name_lower for keyword in interest_keywords):
                        is_recommended = True

                if is_recommended:
                    recommended_resource_ids.add(resource_id)

        else:
            timeline_estimate = "No steps defined for this path."

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
                          recommended_resource_ids=recommended_resource_ids,
                          is_homepage=False,
                          body_class='in-app-layout')

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
                code = str(random.randint(1000, 9999))
                expiry = datetime.utcnow() + timedelta(minutes=15)
                user.verification_code = code
                user.verification_code_expiry = expiry
                db.session.commit()

                email_sent = send_email(
                    to=user.email,
                    subject='Your Careerpath! Verification Code',
                    template_prefix='email/verify_code',
                    user=user,
                    code=code
                )

                if email_sent:
                    flash('Account created! Please check your email for the verification code.', 'success')
                else:
                    flash('Account created, but verification code email could not be sent. Please contact support.', 'warning')

                return redirect(url_for('verify_code_entry', email=user.email))

            except Exception as e_code:
                db.session.rollback()
                print(f"Error generating/sending verification code for {user.email}: {e_code}")
                flash('Account created, but failed to send verification code. Please contact support.', 'warning')
                return redirect(url_for('login'))

        except Exception as e:
            db.session.rollback()
            print(f"Error during registration: {e}")
            flash('An error occurred during registration. Please try again.', 'danger')
    return render_template('register.html', title='Register', form=form, is_homepage=False)

# --- NEW Initial Code Verification Route ---
@app.route('/verify-code', methods=['GET', 'POST'])
def verify_code_entry():
    """Handles the initial email verification code entry after registration."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    form = VerifyCodeForm()
    email = request.args.get('email')

    if form.validate_on_submit():
        if not email:
            flash("Could not identify user. Please try logging in.", "warning")
            return redirect(url_for('login'))

        user = User.query.filter_by(email=email).first()
        submitted_code = form.code.data

        if not user:
            flash("User not found. Please register or check the email address.", "danger")
            return redirect(url_for('login'))

        if user.verification_code == submitted_code and \
           user.verification_code_expiry and \
           user.verification_code_expiry > datetime.utcnow():
            try:
                user.email_verified = True
                user.verification_code = None
                user.verification_code_expiry = None
                db.session.commit()
                flash("Email verified successfully! Please log in.", "success")
                return redirect(url_for('login'))
            except Exception as e:
                db.session.rollback()
                print(f"Error verifying email for {email}: {e}")
                flash("An error occurred during verification. Please try again.", 'danger')
        else:
            flash("Invalid or expired verification code.", "danger")

    return render_template('verify_code.html',
                          title="Verify Your Email",
                          form=form,
                          email=email,
                          is_homepage=False)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = LoginForm()

    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            user.last_login = datetime.utcnow()
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error updating last_login: {e}")

            # --- << NEW: Check if email verified AFTER login >> ---
            if not user.email_verified:
                try:
                    # Generate and send a NEW code
                    code = str(random.randint(1000, 9999))
                    expiry = datetime.utcnow() + timedelta(minutes=15) # Use timedelta here
                    user.verification_code = code
                    user.verification_code_expiry = expiry
                    db.session.commit() # Save new code/expiry

                    email_sent = send_email(
                        to=user.email,
                        subject='Verify Your Email for Careerpath!',
                        template_prefix='email/verify_code', # Reuse code email template
                        user=user,
                        code=code
                    )
                    if email_sent:
                        flash('Login successful, but please verify your email to continue. A new code has been sent.', 'warning')
                    else:
                         flash('Login successful, but email verification is required and we failed to send a new code. Please contact support.', 'danger')

                except Exception as e_verify:
                    db.session.rollback()
                    print(f"Error sending verification code during login for {user.email}: {e_verify}")
                    flash('Login successful, but there was an error initiating email verification.', 'danger')

                # Redirect to the required verification page
                return redirect(url_for('verify_code_required'))
            # --- << END Verification Check >> ---
            else:
                # Email IS verified, proceed as normal
                flash('Login Successful!', 'success')
                next_page = request.args.get('next')
                if next_page and not (next_page.startswith('/') or next_page.startswith(request.host_url)):
                     next_page = None
                if not user.onboarding_complete:
                     return redirect(url_for('onboarding'))
                else:
                     return redirect(next_page or url_for('dashboard'))
        else:
            flash('Login Unsuccessful. Please check email and password.', 'danger')
    # If GET or form invalid
    return render_template('login.html', title='Login', form=form, is_homepage=False) # Use dark navbar


# --- NEW Logged-In Code Verification Route ---
@app.route('/verify-code-required', methods=['GET', 'POST'])
@login_required # User must be logged in to reach here
def verify_code_required():
    """Handles verification code entry when required after login."""

    # Redirect if already verified (shouldn't happen if logic is right, but safe check)
    if current_user.email_verified:
        return redirect(url_for('dashboard'))

    form = VerifyCodeForm()

    if form.validate_on_submit():
        submitted_code = form.code.data
        # Check code against the logged-in user's record
        if current_user.verification_code == submitted_code and \
           current_user.verification_code_expiry and \
           current_user.verification_code_expiry > datetime.utcnow():
            try:
                # Success! Verify email and clear code/expiry
                current_user.email_verified = True
                current_user.verification_code = None
                current_user.verification_code_expiry = None
                db.session.commit()
                flash("Email verified successfully! Welcome to your dashboard.", "success")
                # Redirect to dashboard (or originally intended page?)
                # For simplicity, redirect to dashboard for now.
                return redirect(url_for('dashboard'))
            except Exception as e:
                 db.session.rollback()
                 print(f"Error verifying email post-login for {current_user.email}: {e}")
                 flash("An error occurred during verification. Please try again.", 'danger')
        else:
            # Code mismatch or expired
            flash("Invalid or expired verification code. A new code may have been sent if you reloaded.", "danger")
            # Re-render the same page with error
            return redirect(url_for('verify_code_required')) # Redirect GET to potentially show new code message

    # GET request: Show the verification form
    # Optionally resend code if user lands here via GET? Or require login attempt again?
    # For now, just show form. User might need to trigger login again if code expires.
    return render_template('verify_code_required.html',
                            title="Verify Email to Continue",
                            form=form,
                            is_homepage=False, # Use in-app layout
                            body_class='in-app-layout')

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
                        try:
                            os.remove(old_path)
                        except OSError as e:
                            print(f"Error removing old CV {old_path}: {e}")
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
        scores = {"Data Analysis / Analytics": 0, "UX/UI Design": 0, "Software Engineering": 0, "Cybersecurity": 0}
        answers = {'q1': form.q1_hobby.data, 'q2': form.q2_approach.data, 'q3': form.q3_reward.data, 'q4': form.q4_feedback.data}
        if answers['q1'] == 'A':
            scores["Data Analysis / Analytics"] += 1
        elif answers['q1'] == 'B':
            scores["UX/UI Design"] += 1
        elif answers['q1'] == 'C':
            scores["Software Engineering"] += 1
        elif answers['q1'] == 'D':
            scores["Cybersecurity"] += 1
        if answers['q2'] == 'A':
            scores["Data Analysis / Analytics"] += 1
        elif answers['q2'] == 'B':
            scores["UX/UI Design"] += 1
        elif answers['q2'] == 'C':
            scores["Software Engineering"] += 1
        elif answers['q2'] == 'D':
            scores["Cybersecurity"] += 1
        if answers['q3'] == 'A':
            scores["Data Analysis / Analytics"] += 1
        elif answers['q3'] == 'B':
            scores["UX/UI Design"] += 1
        elif answers['q3'] == 'C':
            scores["Software Engineering"] += 1
        elif answers['q3'] == 'D':
            scores["Cybersecurity"] += 1
        if answers['q4'] == 'A':
            scores["Data Analysis / Analytics"] += 1
        elif answers['q4'] == 'B':
            scores["UX/UI Design"] += 1
        elif answers['q4'] == 'C':
            scores["Software Engineering"] += 1
        elif answers['q4'] == 'D':
            scores["Cybersecurity"] += 1

        available_paths = {"Data Analysis / Analytics", "UX/UI Design", "Cybersecurity", "Software Engineering"}
        filtered_scores = {path: score for path, score in scores.items() if path in available_paths and score > 0}

        recommended_paths_info = []

        if not filtered_scores:
            default_path = CareerPath.query.filter_by(name="Data Analysis / Analytics").first()
            if default_path:
                recommended_paths_info.append({'id': default_path.id, 'name': default_path.name})
            flash("Your answers didn't strongly match a specific path, suggesting Data Analysis as a starting point.", "info")
        else:
            max_score = max(filtered_scores.values())
            top_paths_names = [path for path, score in filtered_scores.items() if score == max_score]
            top_paths = CareerPath.query.filter(CareerPath.name.in_(top_paths_names)).all()
            recommended_paths_info = [{'id': p.id, 'name': p.name} for p in top_paths]

            if len(recommended_paths_info) > 1:
                flash(f"You showed strong interest in multiple areas! Explore the recommendations below.", "info")
            elif not recommended_paths_info:
                flash("Could not determine recommendation. Please select a path manually.", "warning")
                return redirect(url_for('onboarding_form'))

        session['recommended_paths'] = recommended_paths_info

        return redirect(url_for('recommendation_results'))

    return render_template('recommendation_test.html',
                          title="Career Recommendation Test",
                          form=form,
                          is_homepage=False,
                          body_class='in-app-layout')

# --- Recommendation Results Route ---
@app.route('/recommendation-results')
@login_required
def recommendation_results():
    """Displays the recommendation results and next steps."""
    recommended_paths_info = session.pop('recommended_paths', None)

    if not recommended_paths_info:
        flash('Recommendation results not found or expired. Please try the test again.', 'warning')
        return redirect(url_for('recommendation_test'))

    is_multiple = len(recommended_paths_info) > 1

    return render_template('recommendation_results.html',
                          title="Your Recommendation",
                          recommended_paths=recommended_paths_info,
                          is_multiple=is_multiple,
                          is_homepage=False,
                          body_class='in-app-layout')

# --- Portfolio Routes ---
def get_portfolio_upload_path(filename):
    portfolio_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    os.makedirs(portfolio_dir, exist_ok=True)
    return os.path.join(portfolio_dir, filename)

@app.route('/portfolio')
@login_required
@plan_required('Starter', 'Pro')
def portfolio():
    """Displays the user's portfolio items."""
    items = PortfolioItem.query.filter_by(user_id=current_user.id).order_by(PortfolioItem.created_at.desc()).all()
    return render_template('portfolio.html', title='My Portfolio', portfolio_items=items, is_homepage=False, body_class='in-app-layout')

# --- Portfolio Add Route ---
@app.route('/portfolio/add', methods=['GET', 'POST'])
@login_required
@plan_required('Starter', 'Pro')
def add_portfolio_item():
    """Handles adding a new portfolio item, optionally linked to a step/milestone."""
    form = PortfolioItemForm()

    step_id_from_url = None
    milestone_id_from_url = None
    linked_item_name = None
    if request.method == 'GET':
        step_id_from_url = request.args.get('step_id', type=int)
        milestone_id_from_url = request.args.get('milestone_id', type=int)
        if step_id_from_url:
            linked_step = Step.query.get(step_id_from_url)
            if linked_step:
                linked_item_name = f"Step: {linked_step.name}"
            else:
                step_id_from_url = None
                flash("Invalid associated step ID provided.", "warning")

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

        assoc_step_id = request.form.get('associated_step_id', type=int)
        assoc_milestone_id = request.form.get('associated_milestone_id', type=int)

        new_item = PortfolioItem(
            user_id=current_user.id,
            title=form.title.data,
            description=form.description.data,
            item_type=form.item_type.data,
            link_url=link_url if link_url else None,
            file_filename=file_filename_to_save,
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
            return render_template('add_edit_portfolio_item.html',
                                  title='Add Portfolio Item',
                                  form=form,
                                  is_edit=False,
                                  step_id=assoc_step_id,
                                  milestone_id=assoc_milestone_id,
                                  linked_item_name=linked_item_name,
                                  is_homepage=False,
                                  body_class='in-app-layout')

    return render_template('add_edit_portfolio_item.html',
                          title='Add Portfolio Item',
                          form=form,
                          is_edit=False,
                          step_id=step_id_from_url,
                          milestone_id=milestone_id_from_url,
                          linked_item_name=linked_item_name,
                          is_homepage=False,
                          body_class='in-app-layout')

@app.route('/portfolio/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
@plan_required('Starter', 'Pro')
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
@plan_required('Starter', 'Pro')
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
@plan_required('Starter', 'Pro')
def download_portfolio_file(item_id):
    """Provides download access to an uploaded portfolio file, checking ownership."""
    item = PortfolioItem.query.get_or_404(item_id)

    if item.user_id != current_user.id:
        abort(403)

    if not item.file_filename:
        flash("No downloadable file associated with this portfolio item.", "warning")
        return redirect(url_for('portfolio'))

    portfolio_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    filename = item.file_filename

    try:
        return send_from_directory(portfolio_dir, filename, as_attachment=True)
    except FileNotFoundError:
        abort(404)
    except Exception as e:
        print(f"Error sending portfolio file {filename}: {e}")
        abort(500)

# --- NEW Pricing Page Route ---
@app.route('/pricing')
def pricing_page():
    """Displays the pricing page."""
    return render_template('pricing.html', title='Pricing', is_homepage=True)

# --- NEW Contact Page Route ---
@app.route('/contact', methods=['GET', 'POST'])
def contact_page():
    """Displays and handles the contact form."""
    form = ContactForm()
    if form.validate_on_submit():
        name = form.name.data
        email = form.email.data
        message = form.message.data

        print(f"Contact Form Submitted:\n Name: {name}\n Email: {email}\n Message: {message}")

        flash("Thank you for your message! We'll get back to you soon.", "success")
        return redirect(url_for('contact_page'))

    return render_template('contact.html', title='Contact Us', form=form, is_homepage=True)

# --- Profile Route ---
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """Displays user profile and handles updates."""
    form = EditProfileForm(obj=current_user)

    if request.method == 'GET' and current_user.target_career_path:
        form.target_career_path.data = current_user.target_career_path

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
                        try:
                            os.remove(old_path)
                            print(f"Removed old CV during profile update: {old_path}")
                        except OSError as e:
                            print(f"Error removing old CV {old_path}: {e}")

                file.save(file_path)
                print(f"New CV saved via profile to: {file_path}")

            current_user.first_name = form.first_name.data
            current_user.last_name = form.last_name.data
            current_user.target_career_path = form.target_career_path.data
            current_user.current_role = form.current_role.data
            current_user.employment_status = form.employment_status.data
            current_user.time_commitment = form.time_commitment.data
            current_user.interests = form.interests.data
            current_user.learning_style = form.learning_style.data if form.learning_style.data else None
            current_user.cv_filename = cv_filename_to_save

            current_user.onboarding_complete = True

            db.session.commit()
            flash('Your profile has been updated successfully!', 'success')
            return redirect(url_for('profile'))

        except Exception as e:
            db.session.rollback()
            print(f"Error during profile update: {e}")
            flash('An error occurred while updating your profile. Please try again.', 'danger')

    return render_template('profile.html',
                          title='Edit Profile',
                          form=form,
                          is_homepage=False,
                          body_class='in-app-layout')

# --- Route to Toggle Step Completion Status (AJAX Version) ---
@app.route('/path/step/<int:step_id>/toggle', methods=['POST'])
@login_required
def toggle_step_status(step_id):
    """Marks a step as complete or incomplete for the current user."""
    step = Step.query.get_or_404(step_id)
    user_status = UserStepStatus.query.filter_by(user_id=current_user.id, step_id=step.id).first()
    new_status = 'not_started'
    flash_message = ''
    milestone_completed_now = False

    try:
        if user_status:
            if user_status.status == 'completed':
                user_status.status = 'not_started'
                user_status.completed_at = None
                new_status = 'not_started'
                flash_message = f'Step "{step.name}" marked as not started.'
            else:
                user_status.status = 'completed'
                user_status.completed_at = datetime.utcnow()
                new_status = 'completed'
                flash_message = f'Step "{step.name}" marked as completed!'
                milestone_completed_now = True
        else:
            user_status = UserStepStatus(
                user_id=current_user.id,
                step_id=step.id,
                status='completed',
                completed_at=datetime.utcnow()
            )
            db.session.add(user_status)
            new_status = 'completed'
            flash_message = f'Step "{step.name}" marked as completed!'
            milestone_completed_now = True

        db.session.commit()

        if milestone_completed_now and step.milestone:
            milestone = step.milestone
            all_milestone_step_ids_query = Step.query.filter_by(milestone_id=milestone.id).with_entities(Step.id)
            all_milestone_step_ids = {step_id for step_id, in all_milestone_step_ids_query.all()}
            total_steps_in_milestone = len(all_milestone_step_ids)

            if total_steps_in_milestone > 0:
                completed_statuses_in_milestone_query = UserStepStatus.query.filter(
                    UserStepStatus.user_id == current_user.id,
                    UserStepStatus.status == 'completed',
                    UserStepStatus.step_id.in_(all_milestone_step_ids)
                ).with_entities(UserStepStatus.step_id)
                completed_count = completed_statuses_in_milestone_query.count()

                if completed_count == total_steps_in_milestone:
                    milestone_completed_now = True
                    flash_message += f' Milestone "{milestone.name}" also complete!'
                else:
                    milestone_completed_now = False
            else:
                milestone_completed_now = False

        updated_milestone_progress = {}
        updated_overall_progress = {}
        if step.milestone:
            m_id = step.milestone.id
            m_total = Step.query.filter_by(milestone_id=m_id).count()
            if m_total > 0:
                m_step_ids_q = Step.query.filter_by(milestone_id=m_id).with_entities(Step.id)
                m_step_ids = {sid for sid, in m_step_ids_q.all()}
                m_completed_q = UserStepStatus.query.filter(UserStepStatus.user_id == current_user.id, UserStepStatus.status == 'completed', UserStepStatus.step_id.in_(m_step_ids)).with_entities(UserStepStatus.step_id)
                m_completed = m_completed_q.count()
                m_percent = round((m_completed / m_total) * 100)
                updated_milestone_progress = {'completed': m_completed, 'total': m_total, 'percent': m_percent}

        if current_user.target_career_path:
            all_path_steps_q = Step.query.join(Milestone).filter(Milestone.career_path_id == current_user.target_career_path_id).with_entities(Step.id)
            all_path_step_ids = {sid for sid, in all_path_steps_q.all()}
            o_total = len(all_path_step_ids)
            if o_total > 0:
                o_completed_q = UserStepStatus.query.filter(
                    UserStepStatus.user_id == current_user.id,
                    UserStepStatus.status == 'completed',
                    UserStepStatus.step_id.in_(all_path_step_ids)
                )
                o_completed = o_completed_q.count()
                o_percent = round((o_completed / o_total) * 100)
                updated_overall_progress = {'completed': o_completed, 'total': o_total, 'percent': o_percent}

        return jsonify({
            'success': True,
            'new_status': new_status,
            'step_id': step.id,
            'milestone_id': step.milestone_id,
            'message': flash_message,
            'milestone_completed': milestone_completed_now,
            'milestone_progress': updated_milestone_progress,
            'overall_progress': updated_overall_progress
        })

    except Exception as e:
        db.session.rollback()
        print(f"Error updating step status via AJAX for user {current_user.id}, step {step_id}: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while updating status.'}), 500

# --- NEW CV Download Route ---
@app.route('/cv-download')
@login_required
def download_cv():
    """Allows the user to download their uploaded CV."""
    if not current_user.cv_filename:
        flash("No CV uploaded.", "warning")
        return redirect(url_for('profile'))

    cv_directory = app.config['UPLOAD_FOLDER']
    filename = current_user.cv_filename

    try:
        return send_from_directory(cv_directory, filename, as_attachment=True)
    except FileNotFoundError:
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
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Deleted CV file: {file_path}")
        else:
            print(f"CV file not found for deletion, but clearing DB record: {file_path}")

        current_user.cv_filename = None
        db.session.commit()
        flash("CV deleted successfully.", "success")

    except OSError as e:
        db.session.rollback()
        print(f"Error deleting CV file {file_path}: {e}")
        flash("An error occurred while deleting the CV file.", "danger")
    except Exception as e:
        db.session.rollback()
        print(f"Error clearing CV filename in DB for user {current_user.id}: {e}")
        flash("An error occurred while updating your profile after CV deletion.", "danger")

    return redirect(url_for('profile'))


# --- NEW Interview Prep Route ---
@app.route('/interview-prep')
@login_required
@plan_required('Pro') # Restrict to Pro plan users
def interview_prep():
    """Displays interview questions relevant to the user's path."""
    general_questions = INTERVIEW_QUESTIONS.get('General', [])
    path_specific_questions = []
    path_name = "Your Target Path" # Default

    if current_user.target_career_path:
        path_name = current_user.target_career_path.name
        # Get questions for the specific path, default to empty list if path name not in dict
        path_specific_questions = INTERVIEW_QUESTIONS.get(path_name, [])

    return render_template('interview_prep.html',
                           title="Interview Preparation",
                           path_name=path_name,
                           general_questions=general_questions,
                           path_specific_questions=path_specific_questions,
                           is_homepage=False,
                           body_class='in-app-layout')


# --- NEW CV Helper Routes ---
@app.route('/cv-helper', methods=['GET', 'POST'])
@login_required
@plan_required('Starter', 'Pro') # Apply plan restriction based on your pricing
def cv_helper():
    """Displays form to paste JD and processes it."""
    form = CVHelperForm()
    if form.validate_on_submit():
        jd_text = form.job_description.data.lower() # Convert JD to lowercase once

        # Basic Keyword Extraction from JD
        extracted_keywords = {kw for kw in COMMON_TECH_KEYWORDS if kw in jd_text}

        # Get User Data (Portfolio Titles/Desc, Interests)
        user_data_text = (current_user.interests or "").lower()
        portfolio_items = PortfolioItem.query.filter_by(user_id=current_user.id).all()
        for item in portfolio_items:
            user_data_text += " " + (item.title or "").lower()
            user_data_text += " " + (item.description or "").lower()
        # Could also include current_role, target_path name etc.

        # Find matches and missing keywords
        matched_keywords = {kw for kw in extracted_keywords if kw in user_data_text}
        missing_keywords = extracted_keywords - matched_keywords

        # Store results in session to display on next page
        session['cv_helper_results'] = {
            'matched': sorted(list(matched_keywords)),
            'missing': sorted(list(missing_keywords)),
            'jd_keywords': sorted(list(extracted_keywords)) # Also store all found in JD
        }
        return redirect(url_for('cv_helper_results'))

    return render_template('cv_helper.html',
                           title="CV Keyword Helper",
                           form=form,
                           is_homepage=False,
                           body_class='in-app-layout')


@app.route('/cv-helper/results')
@login_required
@plan_required('Starter', 'Pro') # Apply same plan restriction
def cv_helper_results():
    """Displays the results of the CV keyword analysis."""
    results = session.pop('cv_helper_results', None) # Get results and clear from session

    if not results:
        flash("No analysis results found. Please submit a job description first.", "warning")
        return redirect(url_for('cv_helper'))

    return render_template('cv_helper_results.html',
                           title="CV Keyword Analysis Results",
                           results=results,
                           is_homepage=False,
                           body_class='in-app-layout')

# --- Password Reset Routes ---
@app.route("/reset_password", methods=['GET', 'POST'])
def request_reset():
    """Route for requesting a password reset."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user:
            try:
                s = Serializer(current_app.config['SECRET_KEY'])
                token_salt = 'password-reset-salt'
                token = s.dumps(user.id, salt=token_salt)
                reset_url = url_for('reset_token', token=token, _external=True)

                email_sent = send_email(
                    to=user.email,
                    subject='Password Reset Request - Careerpath!',
                    template_prefix='email/reset_password',
                    user=user,
                    reset_url=reset_url
                )

                if email_sent:
                    flash('An email has been sent with instructions to reset your password.', 'info')
                else:
                    flash('Could not send password reset email. Please try again later or contact support.', 'danger')

            except Exception as e_token:
                print(f"Error generating reset token or sending email for {user.email}: {e_token}")
                flash('An error occurred processing your request. Please try again.', 'danger')
        else:
            flash('If an account exists for that email, instructions to reset your password have been sent.', 'info')

        return redirect(url_for('login'))

    is_homepage_layout = not current_user.is_authenticated
    return render_template('request_reset.html', title='Reset Password Request', form=form, is_homepage=is_homepage_layout)

@app.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_token(token):
    """Route for resetting password using a token."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    user = User.verify_reset_token(token)

    if user is None:
        flash('That is an invalid or expired token. Please request a new one.', 'warning')
        return redirect(url_for('request_reset'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        try:
            user.set_password(form.password.data)
            db.session.commit()
            flash('Your password has been updated! You are now able to log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            print(f"Error resetting password for user {user.id}: {e}")
            flash('An error occurred while resetting your password. Please try again.', 'danger')

    is_homepage_layout = not current_user.is_authenticated
    return render_template('reset_password.html', title='Reset Password', form=form, token=token, is_homepage=is_homepage_layout)

# --- Main execution ---
if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    portfolio_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portfolio')
    if not os.path.exists(portfolio_upload_dir):
        os.makedirs(portfolio_upload_dir)

    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
