# forms.py
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, SubmitField, SelectField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional
from wtforms_sqlalchemy.fields import QuerySelectField
from models import User, CareerPath
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
# Import the User model to check if email exists during registration
from models import User
# Add URL validator, FileField, FileAllowed, Optional, TextAreaField, SelectField if not already imported
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, SubmitField, SelectField, TextAreaField, URLField
from wtforms.validators import DataRequired, Length, Optional, URL, ValidationError
from wtforms import RadioField, SubmitField
# Import PortfolioItem model if needed for validation (not strictly needed here)
# from models import PortfolioItem

class RegistrationForm(FlaskForm):
    """Form for user registration."""
    first_name = StringField('First Name',
                             validators=[DataRequired(), Length(min=2, max=50)])
    last_name = StringField('Last Name',
                            validators=[DataRequired(), Length(min=2, max=50)])
    email = StringField('Email',
                        validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password',
                                     validators=[DataRequired(), EqualTo('password', message='Passwords must match.')])
    submit = SubmitField('Sign Up')

    def validate_email(self, email):
        """Check if email is already registered."""
        user = User.query.filter_by(email=email.data.lower()).first()
        if user:
            raise ValidationError('That email is already registered. Please choose a different one or log in.')

class LoginForm(FlaskForm):
    """Form for user login."""
    email = StringField('Email',
                        validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Login')

# --- Function to provide query for QuerySelectField ---
def career_path_query():
    # Query active/available career paths, order them if desired
    return CareerPath.query.order_by(CareerPath.name).all()

def get_pk_from_identity(obj):
    """Helper function for QuerySelectField to get the primary key."""
    return obj.id

# --- Onboarding Form Definition ---
class OnboardingForm(FlaskForm):
    """Form for collecting user details after registration."""

    target_career_path = QuerySelectField(
        'Select Your Target Career Path',
        query_factory=career_path_query,
        get_label='name', # Display the 'name' attribute in the dropdown
        get_pk=get_pk_from_identity, # Use the helper function to get the id
        allow_blank=False, # Don't allow an empty selection
        validators=[DataRequired(message="Please select your target career path.")]
    )
    current_role = StringField(
        'Your Current Role/Job Title (or "Student")',
        validators=[DataRequired(), Length(max=100)]
    )
    employment_status = SelectField(
        'Current Employment Status',
        choices=[
            ('', '-- Select Status --'),
            ('Student', 'Student'),
            ('Employed Full-Time', 'Employed Full-Time'),
            ('Employed Part-Time', 'Employed Part-Time'),
            ('Unemployed', 'Unemployed'),
            ('Freelance/Self-employed', 'Freelance/Self-employed'),
            ('Other', 'Other')
        ],
        validators=[DataRequired(message="Please select your employment status.")]
    )
    time_commitment = SelectField(
        'Estimated Weekly Time Commitment for Learning',
        choices=[
            ('', '-- Select Time --'),
            ('<5 hrs', '< 5 hours'),
            ('5-10 hrs', '5 - 10 hours'),
            ('10-15 hrs', '10 - 15 hours'),
            ('15+ hrs', '15+ hours')
        ],
        validators=[DataRequired(message="Please estimate your available time.")]
    )
    interests = TextAreaField(
        'Specific Tech Interests (Optional - e.g., machine learning, mobile design, cloud security)',
        validators=[Optional(), Length(max=500)] # Optional field
    )
    learning_style = SelectField(
        'Preferred Learning Style (Optional)',
        choices=[
            ('', '-- Select Style (Optional) --'),
            ('Visual', 'Visual (Diagrams, Videos)'),
            ('Auditory', 'Auditory (Lectures, Discussions)'),
            ('Reading/Writing', 'Reading/Writing (Articles, Notes)'),
            ('Kinesthetic/Practical', 'Kinesthetic/Practical (Doing, Projects)')
        ],
        validators=[Optional()] # Optional field
    )
    cv_upload = FileField(
        'Upload CV/Resume (Optional - PDF or DOCX, Max 10MB)',
        validators=[
            Optional(), # Optional field
            FileAllowed(['pdf', 'docx'], 'Only PDF and DOCX files are allowed!')
            # Add file size validation if needed (requires custom validator or checking in route)
        ]
    )
    submit = SubmitField('Save Profile & Start Journey')

# --- New Portfolio Item Form ---
class PortfolioItemForm(FlaskForm):
    """Form for adding or editing portfolio items."""
    title = StringField('Title', validators=[DataRequired(), Length(max=200)])
    description = TextAreaField('Description (Optional)', validators=[Optional(), Length(max=1000)]) # Increased length
    item_type = SelectField('Item Type', choices=[
        ('Project', 'Project'),
        ('Certificate', 'Certificate'),
        ('Article', 'Article/Blog Post'),
        ('Presentation', 'Presentation'),
        ('Other', 'Other')
    ], validators=[DataRequired()])
    link_url = URLField('Link URL (e.g., GitHub, Live Demo, Article)', validators=[Optional(), URL(), Length(max=500)])
    # Use item_file name to avoid clash with model field file_filename
    item_file = FileField('Upload File (Optional - e.g., Certificate PDF)', validators=[
        Optional(),
        FileAllowed(['pdf', 'png', 'jpg', 'jpeg', 'gif', 'docx', 'pptx'], 'Allowed file types: pdf, docx, pptx, png, jpg, gif')
        # Add file size check in route if needed
        ])
    submit = SubmitField('Save Item')

    # Custom validator: Ensure either link_url or item_file is provided
    def validate(self, extra_validators=None):
        # Run default validators first
        initial_validation = super(PortfolioItemForm, self).validate(extra_validators)
        if not initial_validation:
            return False

        # Check if at least one of link or file is provided
        if not self.link_url.data and not self.item_file.data:
            # Add error to a field (e.g., link_url) or use form-level errors if preferred
            self.link_url.errors.append('Please provide either a Link URL or upload a File.')
            return False
        return True


# --- New Edit Profile Form ---
class EditProfileForm(FlaskForm):
    """Form for editing user profile information."""
    # Email might be displayed but not editable, or requires specific validation if changeable
    # email = StringField('Email', validators=[DataRequired(), Email()]) # Example if editable

    first_name = StringField('First Name', validators=[DataRequired(), Length(max=50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=50)])

    target_career_path = QuerySelectField(
        'Select Your Target Career Path',
        query_factory=career_path_query,
        get_label='name',
        get_pk=get_pk_from_identity,
        allow_blank=False, # Keep required for now, could change
        validators=[DataRequired(message="Please select your target career path.")]
    )
    current_role = StringField(
        'Your Current Role/Job Title (or "Student")',
        validators=[DataRequired(), Length(max=100)]
    )
    employment_status = SelectField(
        'Current Employment Status',
        choices=[ # Keep choices consistent with OnboardingForm
            ('', '-- Select Status --'),
            ('Student', 'Student'),
            ('Employed Full-Time', 'Employed Full-Time'),
            ('Employed Part-Time', 'Employed Part-Time'),
            ('Unemployed', 'Unemployed'),
            ('Freelance/Self-employed', 'Freelance/Self-employed'),
            ('Other', 'Other')
        ],
        validators=[DataRequired(message="Please select your employment status.")]
    )
    time_commitment = SelectField(
        'Estimated Weekly Time Commitment for Learning',
        choices=[ # Keep choices consistent with OnboardingForm
            ('', '-- Select Time --'),
            ('<5 hrs', '< 5 hours'),
            ('5-10 hrs', '5 - 10 hours'),
            ('10-15 hrs', '10 - 15 hours'),
            ('15+ hrs', '15+ hours')
        ],
        validators=[DataRequired(message="Please estimate your available time.")]
    )
    interests = TextAreaField(
        'Specific Tech Interests (Optional)',
        validators=[Optional(), Length(max=500)]
    )
    learning_style = SelectField(
        'Preferred Learning Style (Optional)',
        choices=[ # Keep choices consistent with OnboardingForm
            ('', '-- Select Style (Optional) --'),
            ('Visual', 'Visual (Diagrams, Videos)'),
            ('Auditory', 'Auditory (Lectures, Discussions)'),
            ('Reading/Writing', 'Reading/Writing (Articles, Notes)'),
            ('Kinesthetic/Practical', 'Kinesthetic/Practical (Doing, Projects)')
        ],
        validators=[Optional()]
    )
    # Separate handling for CV viewing/upload might be better in the route/template
    # We can add a field here if we want the upload as part of this specific form submission
    cv_upload = FileField(
        'Upload New CV/Resume (Optional - Replaces Existing)',
        validators=[
            Optional(),
            FileAllowed(['pdf', 'docx'], 'Only PDF and DOCX files are allowed!')
        ]
    )
    submit = SubmitField('Update Profile')


class RecommendationTestForm(FlaskForm):
    """Form for the revised career recommendation questionnaire."""

    q1_hobby = RadioField(
        'If you had a free afternoon to explore a tech-related topic, which sounds most engaging?',
        choices=[
            ('A', 'Digging into a large dataset (e.g., movie ratings, public transit data) to see what interesting patterns or stories you could uncover.'),
            ('B', 'Sketching out different layouts and interactions for a mobile app idea to make it user-friendly.'),
            ('C', 'Tinkering with code to build a small tool or understand how an existing program works internally.'),
            ('D', 'Reading about recent cyber threats and thinking about how digital systems could be better protected.')
        ],
        validators=[DataRequired(message="Please select an answer.")]
    )

    q2_approach = RadioField(
        "When tackling a complex challenge, what's your typical starting point?",
        choices=[
            ('A', 'First, gather and analyze all the available data and facts related to the problem.'),
            ('B', 'First, try to understand the perspectives and needs of the people affected by the challenge.'),
            ('C', 'First, break the challenge down into smaller, logical steps or components to build a solution.'),
            ('D', 'First, identify the potential risks, weaknesses, or things that could go wrong.')
        ],
        validators=[DataRequired(message="Please select an answer.")]
    )

    q3_reward = RadioField(
        'Which of these activities feels most satisfying or rewarding to you?',
        choices=[
            ('A', "Discovering a key insight or trend from information that wasn't obvious before."),
            ('B', "Creating a smooth, intuitive, and visually appealing experience for someone else."),
            ('C', 'Building something functional that works reliably and solves a specific task.'),
            ('D', 'Finding and fixing a potential vulnerability or making a system more secure.')
        ],
        validators=[DataRequired(message="Please select an answer.")]
    )

    q4_feedback = RadioField(
        'Imagine reviewing a newly launched website. What kind of feedback are you most likely to give first?',
        choices=[
            ('A', 'Comments on whether the data presented is clear, accurate, and easy to interpret.'),
            ('B', 'Feedback on how easy it is to navigate, find information, and whether the layout feels right.'),
            ('C', 'Thoughts on whether the site loads quickly, works correctly on different devices, or if any features seem broken.'),
            ('D', 'Concerns about whether user data seems secure or if there are potential ways the site could be exploited.')
        ],
        validators=[DataRequired(message="Please select an answer.")]
    )

    submit = SubmitField('See My Recommendation')

