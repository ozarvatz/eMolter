from . import app
from . import question_view  # noqa F401
from . import answer_view   # noqa F401
from . import survey_view   # noqa F401
from . import auth_view  # noqa F401
from . import therapist_view  # noqa F401
from . import admin_view  # noqa F401
from . import analytics_view  # noqa F401
from . import patient_report_view  # noqa F401
from . import llm_survey_view           # noqa F401
from . import conversation_relay_view   # noqa F401
from . import question_set_view    # noqa F401
from flask import render_template
from .models import Question


@app.route('/')
def root():
    questions = Question.query.all()
    return render_template('index.html', questions=questions)
