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
    password_hash = db.Column(db.String(128), nullable=False)
    first_name = db.Column(db.String(50), nullable=True) # Allow null initially
    last_name = db.Column(db.String(50), nullable=True) # Allow null initially
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    # Onboarding / Profile Fields
    current_role = db.Column(db.String(100), nullable=True)
    target_career_path_id = db.Column(db.Integer, db.ForeignKey('career_paths.id'), nullable=True)
    interests = db.Column(db.Text, nullable=True) # Store as JSON string or comma-separated? Text for flexibility
    employment_status = db.Column(db.String(50), nullable=True) # e.g., 'Student', 'Employed Full-Time', etc.
    time_commitment = db.Column(db.String(50), nullable=True) # e.g., '<5 hrs', '5-10 hrs'
    learning_style = db.Column(db.String(50), nullable=True) # Optional: 'Visual', 'Auditory', etc.
    cv_filename = db.Column(db.String(255), nullable=True) # Store filename of uploaded CV
    onboarding_complete = db.Column(db.Boolean, default=False, nullable=False)

    # Relationships
    # user_paths = db.relationship('UserPath', backref='user', lazy=True, cascade="all, delete-orphan") # Link to specific path(s) user is on
    target_career_path = db.relationship('CareerPath', backref='users_targeting')

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

    # Relationships (Placeholder - more needed for milestones/resources)
    # milestones = db.relationship('Milestone', backref='career_path', lazy=True, order_by='Milestone.sequence')
    # resources = db.relationship('Resource', backref='career_path', lazy=True)

    def __repr__(self):
        return f'<CareerPath {self.name}>'

# --- Other Models (To be added later) ---
# class UserPath(db.Model): ... # Link User to CareerPath, track progress
# class Milestone(db.Model): ... # Steps within a CareerPath
# class Resource(db.Model): ... # Courses, links, etc. associated with Milestones/Paths
# class UserMilestone(db.Model): ... # Track user completion of Milestones
