from automated_survey_flask.config import config_env_files
from flask import Flask
# from flask_wtf.csrf import CSRFProtect # 1. Import the protector
from flask_sqlalchemy import SQLAlchemy
import os
# db = SQLAlchemy()
app = Flask(__name__)
env = app.config.get("ENV", "production")
app.secret_key = '123456qwertyasdfghzxcvbn1qaz2wsx3edc4rfv5tgb6yhn' # Use a random string

################
#basedir = os.path.abspath(os.path.dirname(__file__))
basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = True
db = SQLAlchemy()
# csrf = CSRFProtect(app) # 2. Initialize it here!
################ 

def prepare_app(environment=env, p_db=db):
    app.config.from_object(config_env_files[environment])
    p_db.init_app(app)
    # load views by importing them
    from . import views # noqa F401

    return app


def save_and_commit(item):
    db.session.add(item)
    db.session.commit()


db.save = save_and_commit

