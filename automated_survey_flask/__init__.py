from automated_survey_flask.config import config_env_files
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_sqlalchemy import SQLAlchemy
import os
# db = SQLAlchemy()
app = Flask(__name__)
env = app.config.get("ENV", "production")

################
#basedir = os.path.abspath(os.path.dirname(__file__))
basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = True
db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
################ 

def prepare_app(environment=env, p_db=db):
    app.config.from_object(config_env_files[environment])

    # Use environment variable for secret key
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', '123456qwertyasdfghzxcvbn1qaz2wsx3edc4rfv5tgb6yhn')

    p_db.init_app(app)

    # Initialize Flask-Login
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    login_manager.login_message = 'Please log in to access this page.'

    # Enable CSRF protection
    csrf.init_app(app)

    # Exempt Twilio webhook endpoints from CSRF
    csrf.exempt('automated_survey_flask.survey_view.voice_survey')
    csrf.exempt('automated_survey_flask.survey_view.handle_speech')
    csrf.exempt('automated_survey_flask.survey_view.handle_realtime_text')
    csrf.exempt('automated_survey_flask.survey_view.non_llm_recording_callback')
    csrf.exempt('automated_survey_flask.llm_survey_view.llm_voice_survey')
    csrf.exempt('automated_survey_flask.llm_survey_view.llm_handle_speech')
    csrf.exempt('automated_survey_flask.llm_survey_view.llm_recording_callback')

    # load views by importing them
    from . import views # noqa F401

    return app


@login_manager.user_loader
def load_user(user_id):
    from automated_survey_flask.models import User
    return User.query.filter_by(id=int(user_id), deleted=False).first()


def save_and_commit(item):
    db.session.add(item)
    db.session.commit()


db.save = save_and_commit

