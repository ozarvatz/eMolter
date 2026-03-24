from automated_survey_flask import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


class Survey(db.Model):
    __tablename__ = 'surveys'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String, nullable=False)
    questions = db.relationship('Question', backref='survey', lazy='dynamic')

    def __init__(self, title):
        self.title = title

    @property
    def has_questions(self):
        return self.questions.count() > 0


class Question(db.Model):
    __tablename__ = 'questions'

    TEXT = 'text'
    NUMERIC = 'numeric'
    BOOLEAN = 'boolean'

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String, nullable=False)
    kind = db.Column(db.Enum(TEXT, NUMERIC, BOOLEAN, name='question_kind'))
    survey_id = db.Column(db.Integer, db.ForeignKey('surveys.id'))
    answers = db.relationship('Answer', backref='question', lazy='dynamic')

    def __init__(self, content, kind=TEXT):
        self.content = content
        self.kind = kind

    def next(self):
        return self.survey.questions.filter(Question.id > self.id).order_by('id').first()


class Answer(db.Model):
    __tablename__ = 'answers'

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String, nullable=False)
    session_id = db.Column(db.String, nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('questions.id'))

    @classmethod
    def update_content(cls, session_id, question_id, content):
        existing_answer = cls.query.filter(
            Answer.session_id == session_id and Answer.question_id == question_id
        ).first()
        existing_answer.content = content
        db.session.add(existing_answer)
        db.session.commit()

    def __init__(self, content, question, session_id):
        self.content = content
        self.question = question
        self.session_id = session_id

############################
# Example of how your models should look
class Patient(db.Model):
    __tablename__ = 'patients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    nickname = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    batch = db.Column(db.String(50), nullable=True)
    language = db.Column(db.String(10), nullable=False)
    therapist_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    deleted = db.Column(db.Boolean, default=False)
    #calls = db.relationship('Call', backref='patient', lazy=True)

    def __init__(self, name, phone, language='he-IL', nickname=None, batch=None, therapist_id=None):
        self.name = name
        self.phone = phone  # FIXED: was phone_number
        self.nickname = nickname
        self.batch = batch
        self.language = language
        self.therapist_id = therapist_id

    @classmethod
    def active(cls):
        return cls.query.filter_by(deleted=False)


class Call(db.Model):
    __tablename__ = 'calls'
    id = db.Column(db.Integer, primary_key=True)
    call_sid = db.Column(db.String(100), unique=False)
    record_sid = db.Column(db.String(100), unique=True)
    question_id = db.Column(db.Integer, unique=False)
    recording_url = db.Column(db.String(400), unique=False)
    conversation_text = db.Column(db.TEXT)
    patient_phone = db.Column(db.String(20), unique=False)
    carrier_phone = db.Column(db.String(20), unique=False)
    questions_file = db.Column(db.String(20), unique=False)
    prosody_results = db.Column(db.JSON, nullable=True)
    is_processed = db.Column(db.Boolean, default=False)
    is_inexcel = db.Column(db.Boolean, default=False)    
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(
        db.DateTime, 
        default=db.func.now(), 
        onupdate=db.func.now()
    )
    # patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'))
    @classmethod
    def add_conversation_text(cls, callId, conversationText):
        existing_call = cls.query.filter(Call.id == callId).first()
        if existing_call:
            # Atomic update: Concatenate text in the database
            if existing_call.conversation_text is None:
                existing_call.conversation_text = ""
            
            existing_call.conversation_text += f"\n{conversationText}"
            db.session.commit()
    @classmethod
    def update_prosody(cls, call_sid, metrics_dict):
        """
        metrics_dict should be a python dictionary: 
        {'f0_mean': 120, 'pitch_range': 45, 'intensity': 70}
        """
        call_record = cls.query.filter_by(call_sid=call_sid).first()
        if call_record:
            call_record.prosody_results = metrics_dict
            call_record.is_processed = True
            db.session.commit()
    
    def __repr__(self):
        return f"<Call(record_sid='{self.record_sid}', patient='{self.patient_phone}', processed={self.is_processed})>"

    def __init__(self, callSid, recordSid, questionId, recordingUrl, conversationText, patientPhone, carrierPhone, questionsFile):
        self.call_sid = callSid
        self.record_sid = recordSid
        self.question_id = questionId
        self.recording_url = recordingUrl
        self.conversation_text = conversationText
        self.patient_phone = patientPhone
        self.carrier_phone = carrierPhone
        self.questions_file = questionsFile


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    nickname = db.Column(db.String(100), nullable=False)
    batch = db.Column(db.String(50), nullable=True)
    language = db.Column(db.String(10), default='he-IL')
    is_superuser = db.Column(db.Boolean, default=False)
    password_hash = db.Column(db.String(255), nullable=False)
    deleted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=db.func.now())

    # Relationship
    patients = db.relationship('Patient',
        primaryjoin='and_(User.id==Patient.therapist_id, Patient.deleted==False)',
        backref='therapist', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @classmethod
    def active(cls):
        return cls.query.filter_by(deleted=False)


class ProsodyParameter(db.Model):
    __tablename__ = 'prosody_parameters'

    id = db.Column(db.Integer, primary_key=True)
    parameter_key = db.Column(db.String(100), unique=True, nullable=False)
    parameter_name = db.Column(db.String(200), nullable=False)
    explanation = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=True)

    def __init__(self, parameter_key, parameter_name, explanation, category=None):
        self.parameter_key = parameter_key
        self.parameter_name = parameter_name
        self.explanation = explanation
        self.category = category

