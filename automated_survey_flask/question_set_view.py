from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from automated_survey_flask import app, db
from automated_survey_flask.models import QuestionSet


def _superuser_required():
    if not current_user.is_authenticated or not current_user.is_superuser:
        abort(403)


def _owned_sets():
    """Return QuestionSet query scoped to what the current user may see/edit."""
    if current_user.is_superuser:
        return QuestionSet.query
    return QuestionSet.query.filter_by(created_by_id=current_user.id)


def _check_ownership(qs):
    """Abort 403 if the current user doesn't own this QuestionSet."""
    if not current_user.is_superuser and qs.created_by_id != current_user.id:
        abort(403)


def _build_content_from_form(form):
    """Reconstruct the JSON content dict from the submitted form fields."""
    title      = form.get('title', '')
    voice_algo = form.get('voice_algo', '')

    messages = [
        {"body": form.get('msg_sorry_failed', ''), "type": "voice", "message": "sorry_failed"},
        {"body": form.get('msg_you_said', ''),     "type": "voice", "message": "you_said"},
        {"body": form.get('msg_thanks', ''),        "type": "voice", "message": "thanks"},
        {"body": form.get('msg_hello', ''),         "type": "voice", "message": "hello"},
    ]

    # Collect question_0, question_1, … in order
    questions = []
    i = 0
    while True:
        body = form.get(f'question_{i}', '').strip()
        if body:
            questions.append({"body": body, "type": "voice"})
        elif f'question_{i}' not in form:
            break
        i += 1

    return {
        "title":     title,
        "questions": questions,
        "messages":  messages,
        "config":    [{"body": voice_algo, "message": "voice_algo"}],
    }


@app.route('/question-sets')
@login_required
def question_set_list():
    question_sets = _owned_sets().order_by(QuestionSet.batch, QuestionSet.lang).all()
    return render_template('question_set_list.html', question_sets=question_sets)


@app.route('/question-sets/new', methods=['GET', 'POST'])
@login_required
def question_set_new():
    if request.method == 'POST':
        batch = request.form.get('batch', '').strip()
        lang  = request.form.get('lang', '').strip()

        if not batch or not lang:
            flash('Batch and language are required.', 'error')
            return render_template('question_set_form.html', qs=None)

        if QuestionSet.query.filter_by(batch=batch, lang=lang).first():
            flash(f'A question set for batch="{batch}" lang="{lang}" already exists.', 'error')
            return render_template('question_set_form.html', qs=None)

        content = _build_content_from_form(request.form)
        qs = QuestionSet(batch=batch, lang=lang, content=content,
                         created_by_id=current_user.id)
        db.session.add(qs)
        db.session.commit()
        flash(f'Question set "{batch} / {lang}" created.', 'success')
        return redirect(url_for('question_set_list'))

    return render_template('question_set_form.html', qs=None)


@app.route('/question-sets/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def question_set_edit(id):
    qs = QuestionSet.query.get_or_404(id)
    _check_ownership(qs)

    if request.method == 'POST':
        batch = request.form.get('batch', '').strip()
        lang  = request.form.get('lang', '').strip()

        if not batch or not lang:
            flash('Batch and language are required.', 'error')
            return render_template('question_set_form.html', qs=qs)

        # Check uniqueness only if batch/lang changed
        conflict = QuestionSet.query.filter_by(batch=batch, lang=lang).first()
        if conflict and conflict.id != id:
            flash(f'A question set for batch="{batch}" lang="{lang}" already exists.', 'error')
            return render_template('question_set_form.html', qs=qs)

        qs.batch   = batch
        qs.lang    = lang
        qs.content = _build_content_from_form(request.form)
        db.session.commit()
        flash(f'Question set "{batch} / {lang}" updated.', 'success')
        return redirect(url_for('question_set_list'))

    return render_template('question_set_form.html', qs=qs)


@app.route('/question-sets/<int:id>/delete', methods=['POST'])
@login_required
def question_set_delete(id):
    qs = QuestionSet.query.get_or_404(id)
    _check_ownership(qs)
    db.session.delete(qs)
    db.session.commit()
    flash(f'Question set "{qs.batch} / {qs.lang}" deleted.', 'success')
    return redirect(url_for('question_set_list'))
