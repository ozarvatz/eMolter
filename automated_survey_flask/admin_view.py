from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from functools import wraps
from automated_survey_flask import app, db
from automated_survey_flask.models import User, LlmPrompt

SUPPORTED_LANGS = [('he-IL', 'Hebrew'), ('en-US', 'English'), ('de-DE', 'German')]
PROMPT_PLACEHOLDERS = ['{lang}', '{numbered}', '{q_num}', '{max_questions}', '{history_lines}', '{last_answer}']


def superuser_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_superuser:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


@app.route('/admin/therapists')
@login_required
@superuser_required
def therapist_list():
    therapists = User.active().all()
    return render_template('therapist_list.html', therapists=therapists)


@app.route('/admin/therapists/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@superuser_required
def therapist_edit(id):
    therapist = User.query.filter_by(id=id, deleted=False).first_or_404()

    if request.method == 'POST':
        therapist.nickname = request.form.get('nickname')
        therapist.phone = request.form.get('phone')
        therapist.batch = request.form.get('batch')
        therapist.language = request.form.get('language')
        therapist.is_superuser = request.form.get('is_superuser') == 'on'

        new_password = request.form.get('password')
        if new_password:
            therapist.set_password(new_password)

        db.session.commit()
        flash(f'Therapist {therapist.nickname} updated successfully', 'success')
        return redirect(url_for('therapist_list'))

    return render_template('therapist_form.html', therapist=therapist)


@app.route('/admin/therapists/<int:id>/delete', methods=['POST'])
@login_required
@superuser_required
def therapist_delete(id):
    if id == current_user.id:
        flash('Cannot delete yourself', 'error')
        return redirect(url_for('therapist_list'))

    therapist = User.query.filter_by(id=id, deleted=False).first_or_404()
    therapist.deleted = True
    db.session.commit()
    flash(f'Therapist {therapist.nickname} deleted successfully', 'success')
    return redirect(url_for('therapist_list'))


# ---------------------------------------------------------------------------
# LLM Prompts — per-language Groq system/user templates. Only one row per
# language has active=True. Editing/activating is superuser-only.
# ---------------------------------------------------------------------------
@app.route('/admin/prompts')
@login_required
@superuser_required
def prompt_list():
    prompts = LlmPrompt.query.order_by(
        LlmPrompt.lang,
        LlmPrompt.name,
        LlmPrompt.active.desc(),
        LlmPrompt.updated_at.desc(),
    ).all()
    return render_template(
        'prompt_list.html',
        prompts=prompts,
        supported_langs=SUPPORTED_LANGS,
    )


@app.route('/admin/prompts/new', methods=['GET', 'POST'])
@login_required
@superuser_required
def prompt_new():
    if request.method == 'POST':
        return _save_prompt(None)
    return render_template(
        'prompt_form.html',
        prompt=None,
        supported_langs=SUPPORTED_LANGS,
        placeholders=PROMPT_PLACEHOLDERS,
    )


@app.route('/admin/prompts/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@superuser_required
def prompt_edit(id):
    prompt = LlmPrompt.query.get_or_404(id)
    if request.method == 'POST':
        return _save_prompt(prompt)
    return render_template(
        'prompt_form.html',
        prompt=prompt,
        supported_langs=SUPPORTED_LANGS,
        placeholders=PROMPT_PLACEHOLDERS,
    )


@app.route('/admin/prompts/<int:id>/activate', methods=['POST'])
@login_required
@superuser_required
def prompt_activate(id):
    prompt = LlmPrompt.query.get_or_404(id)
    # Deactivate any other row with the SAME (lang, name) — i.e. only one
    # active version of each persona, not one active per lang globally.
    LlmPrompt.query.filter_by(lang=prompt.lang, name=prompt.name).update({'active': False})
    prompt.active = True
    prompt.updated_by_id = current_user.id
    db.session.commit()
    flash(f'Activated prompt #{prompt.id} ({prompt.lang} / {prompt.name})', 'success')
    return redirect(url_for('prompt_list'))


@app.route('/admin/prompts/<int:id>/delete', methods=['POST'])
@login_required
@superuser_required
def prompt_delete(id):
    prompt = LlmPrompt.query.get_or_404(id)
    if prompt.active:
        flash('Cannot delete an active prompt. Activate another one first.', 'error')
        return redirect(url_for('prompt_list'))
    db.session.delete(prompt)
    db.session.commit()
    flash(f'Deleted prompt #{id}', 'success')
    return redirect(url_for('prompt_list'))


def _save_prompt(prompt):
    """Shared create/update logic. If activating, deactivate other rows with
    the same (lang, name) — only one active version per persona."""
    lang   = request.form.get('lang', '').strip()
    name   = (request.form.get('name', '').strip() or LlmPrompt.DEFAULT_NAME)
    system = request.form.get('system_template', '')
    first  = request.form.get('user_template_first', '')
    follow = request.form.get('user_template_followup', '')
    notes  = request.form.get('notes', '').strip() or None
    active = request.form.get('active') == 'on'

    if not lang or not system or not first or not follow:
        flash('Language and all three templates are required.', 'error')
        return redirect(request.url)

    # Lightweight name validation — kebab/snake-case + digits, no spaces or
    # weird chars. Keeps URLs and dropdowns clean.
    import re as _re
    if not _re.match(r'^[a-z0-9_-]+$', name):
        flash('Prompt name must be lowercase letters, digits, hyphens, or underscores only.', 'error')
        return redirect(request.url)

    # (lang, name) uniqueness — enforced by the DB, but check explicitly so
    # we can return a friendly message instead of an IntegrityError.
    existing = LlmPrompt.query.filter_by(lang=lang, name=name).first()
    if existing and (prompt is None or existing.id != prompt.id):
        flash(f'A prompt named "{name}" already exists for {lang}. '
              f'Edit that one or pick a different name.', 'error')
        return redirect(request.url)

    if prompt is None:
        prompt = LlmPrompt(lang=lang)
        db.session.add(prompt)

    prompt.lang                   = lang
    prompt.name                   = name
    prompt.system_template        = system
    prompt.user_template_first    = first
    prompt.user_template_followup = follow
    prompt.notes                  = notes
    prompt.updated_by_id          = current_user.id

    if active:
        # Deactivate other rows for the SAME (lang, name) — i.e. other
        # versions of this persona. Other personas keep their state.
        LlmPrompt.query.filter(
            LlmPrompt.lang == lang,
            LlmPrompt.name == name,
            LlmPrompt.id != (prompt.id or -1),
        ).update({'active': False})
        prompt.active = True
    else:
        prompt.active = False

    db.session.commit()
    flash(f'Prompt saved for {lang} / {name} (active={prompt.active})', 'success')
    return redirect(url_for('prompt_list'))
