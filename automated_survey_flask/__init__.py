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
    csrf.exempt('automated_survey_flask.llm_survey_view.llm_next_question')
    csrf.exempt('automated_survey_flask.llm_survey_view.llm_recording_callback')
    csrf.exempt('automated_survey_flask.conversation_relay_view.llm_relay_voice')
    csrf.exempt('automated_survey_flask.conversation_relay_view.ws_media_stream')

    # load views by importing them
    from . import views # noqa F401

    # Warm up the Groq client so the first patient turn doesn't pay the
    # ~700ms cold-start cost (Python-side `import groq`, httpx pool setup,
    # and the initial TLS handshake to api.groq.com). We fire a 1-token
    # chat completion in a background thread so a slow/unreachable Groq
    # doesn't block server boot.
    def _warm_groq():
        try:
            from automated_survey_flask.llm_survey_view import _get_groq_client, LLM_MODEL
            t0 = __import__('time').time()
            client = _get_groq_client()
            # Tiny real call → establishes the HTTPS connection pool.
            client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": "."}],
                max_tokens=1,
                temperature=0.0,
            )
            print(f"[BOOT] Groq warmed up in {(__import__('time').time()-t0)*1000:.0f}ms")
        except Exception as e:
            print(f"[BOOT] Groq warmup skipped ({type(e).__name__}): {e}")
    import threading
    threading.Thread(target=_warm_groq, daemon=True, name='groq-warmup').start()

    return app


@login_manager.user_loader
def load_user(user_id):
    from automated_survey_flask.models import User
    return User.query.filter_by(id=int(user_id), deleted=False).first()


def save_and_commit(item):
    db.session.add(item)
    db.session.commit()


db.save = save_and_commit

# Auto-prepare when loaded by gunicorn (or any direct import).
# Tests override this by calling prepare_app(environment='testing') afterwards.
if os.environ.get('FLASK_TESTING') != '1':
    prepare_app()

