from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from functools import wraps
from automated_survey_flask import app, db
from automated_survey_flask.models import User


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
