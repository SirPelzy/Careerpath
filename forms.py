# forms.py

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
