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

# Import Models (Make sure Resource, Milestone, Step are imported)
from models import db, User, CareerPath, Milestone, Step, Resource

# Import Forms
from forms import RegistrationForm, LoginForm, OnboardingForm

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# --- Configuration ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'fallback_secret_key_for_development_123') # Use env var ideally
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
if not app.config['SQLALCHEMY_DATABASE_URI']:
    raise ValueError("No DATABASE_URL set for Flask application")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 # Limit uploads to 10 MB

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
    return {'now': datetime.datetime.utcnow}

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

    # --- Fetch and Display Path Logic (will be added later) ---
    target_path = current_user.target_career_path # Get the path object selected during onboarding
    milestones = []
    if target_path:
         # Fetch milestones ordered by sequence for the user's target path
        milestones = Milestone.query.filter_by(career_path_id=target_path.id).order_by(Milestone.sequence).all()
        # We'll fetch step statuses later or within the template using relationships

    # --- Timeline Estimation Logic (Placeholder for now) ---
    timeline_estimate = "Timeline calculation coming soon..."
    # (Logic from previous response will go here later)

    # Render a placeholder or the actual dashboard template
    # return render_template('dashboard.html', user=current_user, path=target_path, milestones=milestones, timeline_estimate=timeline_estimate)
    return f"Welcome to your Dashboard, {current_user.first_name}! Path: {target_path.name if target_path else 'None Selected'}. Milestones loaded: {len(milestones)}" # Simple placeholder

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
            db.session.commit() # Commit last_login update
            next_page = request.args.get('next')
            if next_page and not next_page.startswith('/'): # Basic security check
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
             print(f"Error during onboarding save: {e}")
             flash('An error occurred while saving your profile. Please try again.', 'danger')

    return render_template('onboarding.html', title='Complete Your Profile', form=form)


# --- <<< TEMPORARY ADMIN ROUTE FOR DB INIT & SEEDING >>> ---
# Generate a unique key once and put it here or better, in your .env file
# Example: python -c "import uuid; print(uuid.uuid4())"
INIT_DB_SECRET_KEY = os.environ.get('INIT_DB_SECRET_KEY', 'replace-this-with-a-very-secret-key-9876') # Use a real secret

@app.route(f'/admin/init-db/{INIT_DB_SECRET_KEY}') # Use the secret path
def init_database():
    """Temporary route to initialize the database and seed path data."""
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
            print("Checking for Data Analytics path seeding...")
            da_path = CareerPath.query.filter_by(name="Data Analysis / Analytics").first()

            if da_path and not Milestone.query.filter_by(career_path_id=da_path.id).first():
                print(f"Seeding path for '{da_path.name}'...")

                # Use lists to collect objects for bulk add
                milestones_to_add = []
                steps_to_add = []
                resources_to_add = []

                # Milestone 1: Introduction & Foundation
                m1 = Milestone(name="Introduction & Foundation", sequence=10, career_path_id=da_path.id)
                milestones_to_add.append(m1)
                db.session.add(m1)
                db.session.flush() # Get m1.id
                s1_1 = Step(name="Understand the Data Analyst Role", sequence=10, estimated_time_minutes=60, milestone_id=m1.id)
                s1_2 = Step(name="Find Your Tech Fit", sequence=20, estimated_time_minutes=30, milestone_id=m1.id)
                s1_3 = Step(name="Learn How to Learn Effectively", sequence=30, estimated_time_minutes=30, milestone_id=m1.id)
                steps_to_add.extend([s1_1, s1_2, s1_3])
                db.session.add_all(steps_to_add[-3:]) # Add only the new ones
                db.session.flush() # Get step ids
                resources_to_add.append(Resource(name="Intro Course (Udemy)", url="https://www.udemy.com/share/106Gg8/", resource_type="Course", step_id=s1_1.id))
                resources_to_add.append(Resource(name="Determining Right Tech Career (Medium)", url="https://medium.com/@Sameerah_writes/how-to-determine-the-right-tech-career-for-you-1a7ad90afd75", resource_type="Article", step_id=s1_2.id))
                resources_to_add.append(Resource(name="Best Practices for Tech Learning (Medium)", url="https://medium.com/@Sameerah_writes/best-learning-practices-for-tech-courses-in-2023-c9a908f179db", resource_type="Article", step_id=s1_3.id))

                # Milestone 2: Excel for Data Analysis
                m2 = Milestone(name="Excel for Data Analysis", sequence=20, career_path_id=da_path.id)
                milestones_to_add.append(m2)
                db.session.add(m2)
                db.session.flush()
                s2_1 = Step(name="Excel Basics Tutorial", sequence=10, estimated_time_minutes=180, milestone_id=m2.id)
                s2_2 = Step(name="Excel Lookup Formulas", sequence=20, estimated_time_minutes=60, milestone_id=m2.id)
                s2_3 = Step(name="Excel Charting Techniques", sequence=30, estimated_time_minutes=60, milestone_id=m2.id)
                s2_4 = Step(name="Data Preparation in Excel", sequence=40, estimated_time_minutes=120, milestone_id=m2.id)
                s2_5 = Step(name="Guided Project: Excel Data Analysis", sequence=50, estimated_time_minutes=120, milestone_id=m2.id)
                steps_to_add.extend([s2_1, s2_2, s2_3, s2_4, s2_5])
                db.session.add_all(steps_to_add[-5:])
                db.session.flush()
                resources_to_add.append(Resource(name="Excel Basic Tutorial (Alex YT - Placeholder)", url="#placeholder_yt_link_0", resource_type="Video", step_id=s2_1.id))
                resources_to_add.append(Resource(name="Excel Lookup Formulas (LeilaG YT - Placeholder)", url="#placeholder_yt_link_2", resource_type="Video", step_id=s2_2.id))
                resources_to_add.append(Resource(name="Excel Charts (LeilaG YT - Placeholder)", url="#placeholder_yt_link_4", resource_type="Video", step_id=s2_3.id))
                resources_to_add.append(Resource(name="Data Prep Course (DataCamp)", url="https://campus.datacamp.com/courses/data-preparation-in-excel/starting-data-preparation-in-excel?ex=1#", resource_type="Course", step_id=s2_4.id))
                resources_to_add.append(Resource(name="Excel Guided Project (Coursera - Placeholder)", url="#placeholder_yt_link_5", resource_type="Project", step_id=s2_5.id))

                # Milestone 3: SQL Fundamentals
                m3 = Milestone(name="SQL Fundamentals", sequence=30, career_path_id=da_path.id)
                milestones_to_add.append(m3)
                db.session.add(m3)
                db.session.flush()
                s3_1 = Step(name="SQL Basics Tutorial", sequence=10, estimated_time_minutes=240, milestone_id=m3.id)
                s3_2 = Step(name="Intermediate SQL Tutorial", sequence=20, estimated_time_minutes=180, milestone_id=m3.id)
                s3_3 = Step(name="Advanced SQL Tutorial", sequence=30, estimated_time_minutes=180, milestone_id=m3.id)
                s3_4 = Step(name="Comprehensive SQL Tutorial", sequence=40, estimated_time_minutes=300, milestone_id=m3.id)
                s3_5 = Step(name="Practice SQL Skills", sequence=50, estimated_time_minutes=600, milestone_id=m3.id)
                steps_to_add.extend([s3_1, s3_2, s3_3, s3_4, s3_5])
                db.session.add_all(steps_to_add[-5:])
                db.session.flush()
                resources_to_add.append(Resource(name="SQL Basic Tutorial (Alex YT - Placeholder)", url="#placeholder_yt_link_6", resource_type="Video", step_id=s3_1.id))
                resources_to_add.append(Resource(name="SQL Intermediate Tutorial (Alex YT - Placeholder)", url="#placeholder_yt_link_7", resource_type="Video", step_id=s3_2.id))
                resources_to_add.append(Resource(name="Advanced SQL Tutorial (Alex YT - Placeholder)", url="#placeholder_yt_link_8", resource_type="Video", step_id=s3_3.id))
                resources_to_add.append(Resource(name="SQL from start to finish (DataLemur)", url="https://datalemur.com/sql-tutorial", resource_type="Tutorial", step_id=s3_4.id))
                resources_to_add.append(Resource(name="DataLemur SQL Practice", url="https://datalemur.com/sql-interview-questions", resource_type="Practice", step_id=s3_5.id))

                # Milestone 4: Data Visualization with Tableau
                m4 = Milestone(name="Data Visualization with Tableau", sequence=40, career_path_id=da_path.id)
                milestones_to_add.append(m4)
                db.session.add(m4)
                db.session.flush()
                s4_1 = Step(name="Official Tableau Training", sequence=10, estimated_time_minutes=480, milestone_id=m4.id)
                s4_2 = Step(name="Tableau Beginner Tutorial", sequence=20, estimated_time_minutes=180, milestone_id=m4.id)
                s4_3 = Step(name="Guided Project: Tableau Dashboard", sequence=30, estimated_time_minutes=180, milestone_id=m4.id)
                s4_4 = Step(name="Guided Project: Tableau Beginner Project", sequence=40, estimated_time_minutes=120, milestone_id=m4.id)
                steps_to_add.extend([s4_1, s4_2, s4_3, s4_4])
                db.session.add_all(steps_to_add[-4:])
                db.session.flush()
                resources_to_add.append(Resource(name="Tableau Free Training Videos", url="https://www.tableau.com/learn/training", resource_type="Course", step_id=s4_1.id))
                resources_to_add.append(Resource(name="Tableau for Beginners (Alex YT - Placeholder)", url="#placeholder_yt_link_9", resource_type="Video", step_id=s4_2.id))
                resources_to_add.append(Resource(name="Creating Dashboards (Alex YT - Placeholder)", url="#placeholder_yt_link_11", resource_type="Project", step_id=s4_3.id))
                resources_to_add.append(Resource(name="Tableau Beginner Project (Alex YT - Placeholder)", url="#placeholder_yt_link_12", resource_type="Project", step_id=s4_4.id))

                # Milestone 5: Data Visualization with Power BI
                m5 = Milestone(name="Data Visualization with Power BI", sequence=50, career_path_id=da_path.id)
                milestones_to_add.append(m5)
                db.session.add(m5)
                db.session.flush()
                s5_1 = Step(name="Learn Power BI Fundamentals", sequence=10, estimated_time_minutes=600, milestone_id=m5.id)
                s5_2 = Step(name="Practice Power BI Skills", sequence=20, estimated_time_minutes=300, milestone_id=m5.id, description="Apply learnings from MS Learn modules by building sample dashboards.")
                steps_to_add.extend([s5_1, s5_2])
                db.session.add_all(steps_to_add[-2:])
                db.session.flush()
                resources_to_add.append(Resource(name="Microsoft Learn Power BI Modules (MS - Placeholder)", url="#placeholder_ms_learn_link_13", resource_type="Course", step_id=s5_1.id))

                # Milestone 6: Programming with Python for Data Analysis
                m6 = Milestone(name="Programming with Python for Data Analysis", sequence=60, career_path_id=da_path.id)
                milestones_to_add.append(m6)
                db.session.add(m6)
                db.session.flush()
                s6_1 = Step(name="Python for Beginners Tutorial", sequence=10, estimated_time_minutes=240, milestone_id=m6.id)
                s6_2 = Step(name="Introduction to Pandas & NumPy", sequence=20, estimated_time_minutes=480, milestone_id=m6.id)
                s6_3 = Step(name="Data Visualization with Matplotlib/Seaborn", sequence=30, estimated_time_minutes=300, milestone_id=m6.id)
                s6_4 = Step(name="Guided Project: Python Data Analysis", sequence=40, estimated_time_minutes=300, milestone_id=m6.id)
                s6_5 = Step(name="Basic Web Scraping (Optional)", sequence=50, estimated_time_minutes=120, milestone_id=m6.id)
                steps_to_add.extend([s6_1, s6_2, s6_3, s6_4, s6_5])
                db.session.add_all(steps_to_add[-5:])
                db.session.flush()
                resources_to_add.append(Resource(name="Python for Beginners (Alex YT - Placeholder)", url="#placeholder_yt_link_14", resource_type="Video", step_id=s6_1.id))
                resources_to_add.append(Resource(name="Pandas Tutorial (Data School)", url="https://www.dataschool.io/pandas-tutorial-beginner/", resource_type="Tutorial", step_id=s6_2.id)) # Example link
                resources_to_add.append(Resource(name="NumPy Quickstart", url="https://numpy.org/doc/stable/user/quickstart.html", resource_type="Documentation", step_id=s6_2.id)) # Example link
                resources_to_add.append(Resource(name="Matplotlib Pyplot Tutorial", url="https://matplotlib.org/stable/tutorials/introductory/pyplot.html", resource_type="Tutorial", step_id=s6_3.id)) # Example link
                resources_to_add.append(Resource(name="Seaborn Tutorial", url="https://seaborn.pydata.org/tutorial.html", resource_type="Tutorial", step_id=s6_3.id)) # Example link
                resources_to_add.append(Resource(name="Python Project (Alex YT - Placeholder)", url="#placeholder_yt_link_16", resource_type="Project", step_id=s6_4.id))
                resources_to_add.append(Resource(name="Python Web Scraping Basics (Alex YT - Placeholder)", url="#placeholder_yt_link_15", resource_type="Video", step_id=s6_5.id))

                # Milestone 7: Building Your Data Analytics Portfolio
                m7 = Milestone(name="Building Your Data Analytics Portfolio", sequence=70, career_path_id=da_path.id)
                milestones_to_add.append(m7)
                db.session.add(m7)
                db.session.flush()
                s7_1 = Step(name="Learn Report Structuring", sequence=10, estimated_time_minutes=60, milestone_id=m7.id)
                s7_2 = Step(name="Choose & Set Up Portfolio Platform", sequence=20, estimated_time_minutes=120, milestone_id=m7.id)
                s7_3 = Step(name="Find Datasets for Projects", sequence=30, estimated_time_minutes=60, milestone_id=m7.id)
                s7_4 = Step(name="Complete & Document 2-3 Portfolio Projects", sequence=40, estimated_time_minutes=1200, milestone_id=m7.id)
                steps_to_add.extend([s7_1, s7_2, s7_3, s7_4])
                db.session.add_all(steps_to_add[-4:])
                db.session.flush()
                resources_to_add.append(Resource(name="How I Structure My Data Analytics Reports (Medium - Placeholder)", url="#placeholder_medium_article", resource_type="Article", step_id=s7_1.id))
                resources_to_add.append(Resource(name="How to use Kaggle (YT - Placeholder)", url="#placeholder_yt_link_20", resource_type="Guide", step_id=s7_2.id))
                resources_to_add.append(Resource(name="How to use GitHub (YT - Placeholder)", url="#placeholder_yt_link_22", resource_type="Guide", step_id=s7_2.id)) # Combine 22/23?
                resources_to_add.append(Resource(name="How to use Google Sites (YT - Placeholder)", url="#placeholder_yt_link_24", resource_type="Guide", step_id=s7_2.id))
                resources_to_add.append(Resource(name="Kaggle Datasets", url="https://www.kaggle.com/datasets", resource_type="Resource", step_id=s7_3.id))
                resources_to_add.append(Resource(name="Data.gov", url="https://data.gov/", resource_type="Resource", step_id=s7_3.id))

                # Milestone 8: Gaining Practical Experience
                m8 = Milestone(name="Gaining Practical Experience", sequence=80, career_path_id=da_path.id)
                milestones_to_add.append(m8)
                db.session.add(m8)
                db.session.flush()
                s8_1 = Step(name="Explore Virtual Internships", sequence=10, estimated_time_minutes=60, milestone_id=m8.id)
                s8_2 = Step(name="Complete 1-2 Relevant Virtual Internships", sequence=20, estimated_time_minutes=600, milestone_id=m8.id)
                steps_to_add.extend([s8_1, s8_2])
                db.session.add_all(steps_to_add[-2:])
                db.session.flush()
                resources_to_add.append(Resource(name="Forage Virtual Experiences (Forage - Placeholder)", url="#placeholder_forage_link", resource_type="Platform", step_id=s8_1.id))

                # Milestone 9: Resume & Job Application Strategy
                m9 = Milestone(name="Resume & Job Application Strategy", sequence=90, career_path_id=da_path.id)
                milestones_to_add.append(m9)
                db.session.add(m9)
                db.session.flush()
                s9_1 = Step(name="Craft Your Data Analyst Resume", sequence=10, estimated_time_minutes=180, milestone_id=m9.id)
                s9_2 = Step(name="Optimize Your LinkedIn Profile", sequence=20, estimated_time_minutes=120, milestone_id=m9.id)
                steps_to_add.extend([s9_1, s9_2])
                db.session.add_all(steps_to_add[-2:])
                db.session.flush()
                resources_to_add.append(Resource(name="How to create DA resume (YT - Placeholder)", url="#placeholder_yt_link_26", resource_type="Guide", step_id=s9_1.id))
                resources_to_add.append(Resource(name="Free Resume Template (Placeholder)", url="#placeholder_bitly_link", resource_type="Resource", step_id=s9_1.id))
                resources_to_add.append(Resource(name="LinkedIn Job Tips 1 (YT - Placeholder)", url="#placeholder_yt_link_27", resource_type="Guide", step_id=s9_2.id))
                resources_to_add.append(Resource(name="LinkedIn Job Tips 2 (YT - Placeholder)", url="#placeholder_yt_link_28", resource_type="Guide", step_id=s9_2.id))
                resources_to_add.append(Resource(name="LinkedIn Optimization Thread (Twitter - Placeholder)", url="#placeholder_twitter_link_abiola", resource_type="Guide", step_id=s9_2.id))

                # Milestone 10: Interview Preparation
                m10 = Milestone(name="Interview Preparation", sequence=100, career_path_id=da_path.id)
                milestones_to_add.append(m10)
                db.session.add(m10)
                db.session.flush()
                s10_1 = Step(name="Learn Interview Red Flags & Remote Tips", sequence=10, estimated_time_minutes=60, milestone_id=m10.id)
                s10_2 = Step(name="Practice Data Analyst Interview Questions", sequence=20, estimated_time_minutes=300, milestone_id=m10.id)
                steps_to_add.extend([s10_1, s10_2])
                db.session.add_all(steps_to_add[-2:])
                db.session.flush()
                resources_to_add.append(Resource(name="Interview Red Flags Thread (Twitter - Placeholder)", url="#placeholder_twitter_link_dave", resource_type="Guide", step_id=s10_1.id))
                resources_to_add.append(Resource(name="DA Interview Questions Thread (Twitter - Placeholder)", url="#placeholder_twitter_link_jess", resource_type="Guide", step_id=s10_2.id))

                # Add all resources at the end
                db.session.add_all(resources_to_add)

                db.session.commit() # Commit all additions for the path
                print(f"Path '{da_path.name}' seeded successfully.")

            elif da_path:
                 print(f"Path '{da_path.name}' milestones already seem to exist. Skipping seeding.")
            else:
                 print("Data Analysis career path not found in DB, skipping seeding.")

            flash("Database initialization and seeding check complete.", 'info')
            return redirect(url_for('home')) # Redirect home after completion

        except Exception as e:
            db.session.rollback() # Rollback on any error during the process
            print(f"Error during DB initialization/seeding: {e}")
            flash(f"Error during DB initialization/seeding: {e}", 'danger')
            return redirect(url_for('home')) # Redirect home even on error

# !!! REMEMBER TO REMOVE OR SECURE THIS ROUTE AFTER USE !!!
# --- End of Temporary Init Route ---


# --- Main execution ---
if __name__ == '__main__':
    # Ensure the upload folder exists
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True) # Enable debug mode for development
