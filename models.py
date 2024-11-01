from flask_login import UserMixin
from extensions import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    credits = db.Column(db.Integer, default=10)  # Default 10 credits




    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return True

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return self.id

class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    persona = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user = db.relationship('User', backref='conversations', lazy=True)
    messages = db.relationship('Message', backref='conversation', lazy=True)

    def __init__(self, user_id, persona):
        self.user_id = user_id
        self.persona = persona
        self.created_at = datetime.utcnow()


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    sender = db.Column(db.String(50))
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    conversation = db.relationship('Conversation', backref=db.backref('feedback', lazy=True))

class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    question_english = db.Column(db.Text, nullable=False)
    answer_english = db.Column(db.Text, nullable=False)
    question_hindi = db.Column(db.Text, nullable=False)
    answer_hindi = db.Column(db.Text, nullable=False)


class Persona(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    occupation = db.Column(db.String(100), nullable=False)
    marital_status = db.Column(db.String(100), nullable=False)
    income_range = db.Column(db.String(100), nullable=True)
    dependent_family_members = db.Column(db.String(100), nullable=True)  # Replace location with dependent_family_members
    financial_goals = db.Column(db.String(500), nullable=False)
    category = db.Column(db.String(50), nullable=False)  # Predefined or Custom
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    user = db.relationship('User', backref='personas', lazy=True)



#-----------------------------------------------------------------------
#for reflect

class ReferConversation(db.Model):
    __tablename__ = 'refer_conversations'  # Explicit table name for clarity
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user = db.relationship('User', backref='refer_conversations', lazy=True)
    product = db.relationship('Product', backref='refer_conversations', lazy=True)
    messages = db.relationship('ReferMessage', backref='refer_conversation', lazy=True)

class ReferMessage(db.Model):
    __tablename__ = 'refer_messages'  # Explicit table name
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('refer_conversations.id'), nullable=False)
    sender = db.Column(db.String(50))  # 'user' or 'system' (AI)
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

class ReferFeedback(db.Model):
    __tablename__ = 'reflect_feedback'  # Explicit table name
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('refer_conversations.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    score = db.Column(db.Integer, nullable=False)  # Score out of 100
    category = db.Column(db.String(50), nullable=False)  # Beginner, Proficient, Expert
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    conversation = db.relationship('ReferConversation', backref=db.backref('feedback', lazy=True))

#------------------------------------------------------------------------------------------------------------