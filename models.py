from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer as Serializer
from itsdangerous.exc import SignatureExpired, BadSignature
from flask import current_app # To access SECRET_KEY

# Initialize SQLAlchemy instance - This will be imported and initialized in main.py
db = SQLAlchemy()

class User(UserMixin, db.Model):
    """User model for authentication and profile information."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(50), nullable=True)
    last_name = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    email_verified = db.Column(db.Boolean, nullable=False, default=False, index=True)

    # Onboarding / Profile Fields
    current_role = db.Column(db.String(100), nullable=True)
    target_career_path_id = db.Column(db.Integer, db.ForeignKey('career_paths.id'), nullable=True, index=True)
    interests = db.Column(db.Text, nullable=True)
    employment_status = db.Column(db.String(50), nullable=True)
    time_commitment = db.Column(db.String(50), nullable=True)
    learning_style = db.Column(db.String(50), nullable=True)
    cv_filename = db.Column(db.String(255), nullable=True)
    onboarding_complete = db.Column(db.Boolean, default=False, nullable=False)

    # Relationships
    target_career_path = db.relationship('CareerPath', backref='users_targeting')
    # step_statuses relationship defined via backref in UserStepStatus
    # portfolio_items relationship defined via backref in PortfolioItem

    def set_password(self, password):
        """Hashes the password and stores it."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Checks if the provided password matches the hash."""
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email}>'

    # --- NEW Static Method for Token Verification ---
    @staticmethod
    def verify_reset_token(token, salt='password-reset-salt', max_age_seconds=1800): # 1800 seconds = 30 minutes
        """
        Verifies a password reset token.

        Args:
            token (str): The token received from the user.
            salt (str): Salt used during token generation. Must match.
            max_age_seconds (int): Maximum age of token in seconds.

        Returns:
            User: The user associated with the token if valid and not expired, otherwise None.
        """
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            user_id = s.loads(
                token,
                salt=salt,
                max_age=max_age_seconds
            )
        except SignatureExpired:
            print("Password reset token expired.")
            return None
        except BadSignature:
            print("Password reset token has bad signature.")
            return None
        except Exception as e: # Catch other potential errors
            print(f"Error loading token: {e}")
            return None
        # If loads() successful, return the user
        return User.query.get(user_id)
    # --- END Static Method ---

# --- NEW Static Method for Email Token Verification ---
    @staticmethod
    def verify_email_token(token, salt='email-confirm-salt', max_age_seconds=86400): # 86400 seconds = 24 hours
        """Verifies an email confirmation token."""
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            user_id = s.loads(
                token,
                salt=salt,
                max_age=max_age_seconds
            )
        except SignatureExpired:
            print("Email verification token expired.")
            return None
        except BadSignature:
            print("Email verification token has bad signature.")
            return None
        except Exception as e:
            print(f"Error loading email token: {e}")
            return None
        # Return user if token is valid
        return User.query.get(user_id)
    # --- END Static Method ---


class CareerPath(db.Model):
    """Represents a predefined tech career path."""
    __tablename__ = 'career_paths'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship 'milestones' defined via backref in Milestone model

    def __repr__(self):
        return f'<CareerPath {self.name}>'

# --- Models for Path Structure ---

class Milestone(db.Model):
    """Represents a major stage or module within a CareerPath."""
    __tablename__ = 'milestones'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    sequence = db.Column(db.Integer, nullable=False, default=0)
    career_path_id = db.Column(db.Integer, db.ForeignKey('career_paths.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    career_path = db.relationship('CareerPath', backref=db.backref('milestones', lazy='dynamic', order_by='Milestone.sequence'))

    # --- MODIFIED HERE: Removed lazy='dynamic' ---
    steps = db.relationship('Step', backref='milestone', order_by='Step.sequence', cascade="all, delete-orphan")
    # --- End Modification ---

    # portfolio_items relationship defined via backref in PortfolioItem

    def __repr__(self):
        return f'<Milestone {self.sequence}. {self.name}>'


class Step(db.Model):
    """Represents an individual task or learning item within a Milestone."""
    __tablename__ = 'steps'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    sequence = db.Column(db.Integer, nullable=False, default=0)
    estimated_time_minutes = db.Column(db.Integer, nullable=True)
    milestone_id = db.Column(db.Integer, db.ForeignKey('milestones.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    step_type = db.Column(db.String(50), nullable=True, index=True) # Added via migration

    # Relationships
    resources = db.relationship('Resource', backref='step', lazy='dynamic', cascade="all, delete-orphan")
    # user_statuses defined via backref in UserStepStatus
    # portfolio_items defined via backref in PortfolioItem

    def __repr__(self):
        return f'<Step {self.sequence}. {self.name}>'


class Resource(db.Model):
    """Represents a learning resource (link, tool) associated with a Step."""
    __tablename__ = 'resources'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    resource_type = db.Column(db.String(50), nullable=True)
    step_id = db.Column(db.Integer, db.ForeignKey('steps.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # step relationship defined via backref in Step model

    def __repr__(self):
        return f'<Resource {self.name} ({self.resource_type})>'


# --- Model for User Progress Tracking ---

class UserStepStatus(db.Model):
    """Tracks the completion status of a Step for a specific User."""
    __tablename__ = 'user_step_statuses'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    step_id = db.Column(db.Integer, db.ForeignKey('steps.id'), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='not_started', index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = db.relationship('User', backref=db.backref('step_statuses', lazy='dynamic', cascade="all, delete-orphan"))
    step = db.relationship('Step', backref=db.backref('user_statuses', lazy='dynamic', cascade="all, delete-orphan"))

    __table_args__ = (db.UniqueConstraint('user_id', 'step_id', name='_user_step_uc'),)

    def __repr__(self):
        return f'<UserStepStatus User:{self.user_id} Step:{self.step_id} Status:{self.status}>'


# --- Model for Portfolio Items ---

class PortfolioItem(db.Model):
    """Represents an item in a user's portfolio (project, certificate, etc.)."""
    __tablename__ = 'portfolio_items'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    item_type = db.Column(db.String(50), nullable=False, default='Other')
    link_url = db.Column(db.String(500), nullable=True)
    file_filename = db.Column(db.String(255), nullable=True)
    associated_step_id = db.Column(db.Integer, db.ForeignKey('steps.id'), nullable=True, index=True)
    associated_milestone_id = db.Column(db.Integer, db.ForeignKey('milestones.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = db.relationship('User', backref=db.backref('portfolio_items', lazy='dynamic', cascade="all, delete-orphan"))
    associated_step = db.relationship('Step', backref='portfolio_items')
    associated_milestone = db.relationship('Milestone', backref='portfolio_items')

    def __repr__(self):
        return f'<PortfolioItem {self.id} - {self.title} ({self.user.email})>'
