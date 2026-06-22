from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from automated_survey_flask import app, db
from automated_survey_flask.models import QuestionSet, LlmPrompt


SUPPORTED_LANGS = ['he-IL', 'en-US', 'de-DE']


def _prompt_names_by_lang():
    """Map of lang → ordered list of active prompt names. Used by the QS
    editor to populate the persona dropdown (filtered client-side as the
    user changes language)."""
    return {lang: LlmPrompt.available_names_for(lang) for lang in SUPPORTED_LANGS}


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


def _apply_length_controls(qs, form):
    """Parse and apply the call-length-control fields onto a QuestionSet.
    Returns (ok: bool, error_message: str or None)."""
    mode = (form.get('length_mode') or QuestionSet.LENGTH_BY_COUNT).strip()
    if mode not in (QuestionSet.LENGTH_BY_COUNT, QuestionSet.LENGTH_BY_TIME):
        return False, f"Invalid length_mode: {mode!r}"

    # max_questions: always parsed, sensible bounds
    try:
        max_q = int(form.get('max_questions') or 10)
    except (TypeError, ValueError):
        return False, "max_questions must be an integer."
    if max_q < 1 or max_q > 100:
        return False, "max_questions must be between 1 and 100."

    target = None
    strategy = QuestionSet.EXT_ENGAGEMENT
    if mode == QuestionSet.LENGTH_BY_TIME:
        try:
            target = int(form.get('target_seconds') or 0)
        except (TypeError, ValueError):
            return False, "target_seconds must be an integer."
        if target < 10 or target > 600:
            return False, "target_seconds must be between 10 and 600."

        strategy = (form.get('extension_strategy') or QuestionSet.EXT_ENGAGEMENT).strip()
        if strategy not in (QuestionSet.EXT_ENGAGEMENT,
                            QuestionSet.EXT_REPHRASE,
                            QuestionSet.EXT_MIX):
            return False, f"Invalid extension_strategy: {strategy!r}"

    qs.length_mode        = mode
    qs.target_seconds     = target
    qs.max_questions      = max_q
    qs.extension_strategy = strategy
    return True, None


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
            return render_template('question_set_form.html', qs=None,
                               prompt_names_by_lang=_prompt_names_by_lang())

        if QuestionSet.query.filter_by(batch=batch, lang=lang).first():
            flash(f'A question set for batch="{batch}" lang="{lang}" already exists.', 'error')
            return render_template('question_set_form.html', qs=None,
                               prompt_names_by_lang=_prompt_names_by_lang())

        content = _build_content_from_form(request.form)
        prompt_name = (request.form.get('prompt_name') or '').strip() or None
        qs = QuestionSet(batch=batch, lang=lang, content=content,
                         created_by_id=current_user.id,
                         prompt_name=prompt_name)
        ok, err = _apply_length_controls(qs, request.form)
        if not ok:
            flash(err, 'error')
            return render_template('question_set_form.html', qs=None,
                               prompt_names_by_lang=_prompt_names_by_lang())
        db.session.add(qs)
        db.session.commit()
        flash(f'Question set "{batch} / {lang}" created.', 'success')
        return redirect(url_for('question_set_list'))

    return render_template('question_set_form.html', qs=None,
                               prompt_names_by_lang=_prompt_names_by_lang())


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
            return render_template('question_set_form.html', qs=qs,
                               prompt_names_by_lang=_prompt_names_by_lang())

        # Check uniqueness only if batch/lang changed
        conflict = QuestionSet.query.filter_by(batch=batch, lang=lang).first()
        if conflict and conflict.id != id:
            flash(f'A question set for batch="{batch}" lang="{lang}" already exists.', 'error')
            return render_template('question_set_form.html', qs=qs,
                               prompt_names_by_lang=_prompt_names_by_lang())

        qs.batch       = batch
        qs.lang        = lang
        qs.content     = _build_content_from_form(request.form)
        qs.prompt_name = (request.form.get('prompt_name') or '').strip() or None
        ok, err = _apply_length_controls(qs, request.form)
        if not ok:
            flash(err, 'error')
            return render_template('question_set_form.html', qs=qs,
                               prompt_names_by_lang=_prompt_names_by_lang())
        db.session.commit()
        flash(f'Question set "{batch} / {lang}" updated.', 'success')
        return redirect(url_for('question_set_list'))

    return render_template('question_set_form.html', qs=qs,
                               prompt_names_by_lang=_prompt_names_by_lang())


@app.route('/question-sets/<int:id>/delete', methods=['POST'])
@login_required
def question_set_delete(id):
    qs = QuestionSet.query.get_or_404(id)
    _check_ownership(qs)
    db.session.delete(qs)
    db.session.commit()
    flash(f'Question set "{qs.batch} / {qs.lang}" deleted.', 'success')
    return redirect(url_for('question_set_list'))
