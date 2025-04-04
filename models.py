from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# Initialize SQLAlchemy instance - This will be imported and initialized in main.py
db = SQLAlchemy()

class User(UserMixin, db.Model):
    """User model for authentication and profile information."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    # Ensure password hash length is sufficient (e.g., 255)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(50), nullable=True) # Allow null initially
    last_name = db.Column(db.String(50), nullable=True) # Allow null initially
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    # Onboarding / Profile Fields
    current_role = db.Column(db.String(100), nullable=True)
    target_career_path_id = db.Column(db.Integer, db.ForeignKey('career_paths.id'), nullable=True, index=True) # Added index
    interests = db.Column(db.Text, nullable=True)
    employment_status = db.Column(db.String(50), nullable=True)
    time_commitment = db.Column(db.String(50), nullable=True)
    learning_style = db.Column(db.String(50), nullable=True)
    cv_filename = db.Column(db.String(255), nullable=True)
    onboarding_complete = db.Column(db.Boolean, default=False, nullable=False)

    # Relationships
    target_career_path = db.relationship('CareerPath', backref='users_targeting')
    # step_statuses relationship is defined via backref in UserStepStatus

    def set_password(self, password):
        """Hashes the password and stores it."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Checks if the provided password matches the hash."""
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email}>'


class CareerPath(db.Model):
    """Represents a predefined tech career path."""
    __tablename__ = 'career_paths'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship defined via backref in Milestone model

    def __repr__(self):
        return f'<CareerPath {self.name}>'

# --- New Models for Path Structure ---

class Milestone(db.Model):
    """Represents a major stage or module within a CareerPath."""
    __tablename__ = 'milestones'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    sequence = db.Column(db.Integer, nullable=False, default=0) # For ordering milestones within a path
    career_path_id = db.Column(db.Integer, db.ForeignKey('career_paths.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    # Defines 'milestones' collection on CareerPath, ordered by sequence
    career_path = db.relationship('CareerPath', backref=db.backref('milestones', lazy='dynamic', order_by='Milestone.sequence'))
    # Defines 'steps' collection on Milestone, ordered by sequence
    steps = db.relationship('Step', backref='milestone', lazy='dynamic', order_by='Step.sequence', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Milestone {self.sequence}. {self.name}>'


class Step(db.Model):
    """Represents an individual task or learning item within a Milestone."""
    __tablename__ = 'steps'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    sequence = db.Column(db.Integer, nullable=False, default=0) # For ordering steps within a milestone
    estimated_time_minutes = db.Column(db.Integer, nullable=True) # Optional time estimate
    milestone_id = db.Column(db.Integer, db.ForeignKey('milestones.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    # Defines 'resources' collection on Step
    resources = db.relationship('Resource', backref='step', lazy='dynamic', cascade="all, delete-orphan")
    # user_statuses defined via backref in UserStepStatus

    def __repr__(self):
        return f'<Step {self.sequence}. {self.name}>'


class Resource(db.Model):
    """Represents a learning resource (link, tool) associated with a Step."""
    __tablename__ = 'resources'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    # Type: e.g., 'Course', 'Video', 'Article', 'Tool', 'Documentation', 'Project Idea', 'Guide', 'Platform', 'Practice'
    resource_type = db.Column(db.String(50), nullable=True)
    step_id = db.Column(db.Integer, db.ForeignKey('steps.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # step relationship defined via backref in Step model

    def __repr__(self):
        return f'<Resource {self.name} ({self.resource_type})>'


# --- New Model for User Progress Tracking ---

class UserStepStatus(db.Model):
    """Tracks the completion status of a Step for a specific User."""
    __tablename__ = 'user_step_statuses'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    step_id = db.Column(db.Integer, db.ForeignKey('steps.id'), nullable=False, index=True)
    # Status: 'not_started', 'completed' (could add 'in_progress' later)
    status = db.Column(db.String(20), nullable=False, default='not_started', index=True) # Added index
    completed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    # Defines 'step_statuses' collection on User
    user = db.relationship('User', backref=db.backref('step_statuses', lazy='dynamic', cascade="all, delete-orphan"))
    # Defines 'user_statuses' collection on Step
    step = db.relationship('Step', backref=db.backref('user_statuses', lazy='dynamic', cascade="all, delete-orphan"))

    # Ensure a user can only have one status per step
    __table_args__ = (db.UniqueConstraint('user_id', 'step_id', name='_user_step_uc'),)

    def __repr__(self):
        return f'<UserStepStatus User:{self.user_id} Step:{self.step_id} Status:{self.status}>'
