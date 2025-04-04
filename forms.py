# forms.py
from flask_wtf import FlaskForm
# Import FileField and validators
from flask_wtf.file import FileField, FileAllowed
# Import fields from WTForms
from wtforms import StringField, SubmitField, SelectField, TextAreaField
# Import validators from WTForms
from wtforms.validators import DataRequired, Length, Optional
# Import QuerySelectField
from wtforms_sqlalchemy.fields import QuerySelectField
# Import User model for validation if needed, and CareerPath for QuerySelectField
from models import User, CareerPath
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
# Import the User model to check if email exists during registration
from models import User

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

# --- Onboarding Form (Placeholder - we'll define fields later) ---
class OnboardingForm(FlaskForm):
    """Form for collecting user details after registration."""
    # Define fields like target_career_path, current_role, etc. later
    submit = SubmitField('Save Profile')

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
