from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from automated_survey_flask import app, db
from automated_survey_flask.models import Patient, Call
from twilio.rest import Client
import os
from urllib.parse import quote


def get_patient_or_403(patient_id):
    """Get patient and verify ownership"""
    patient = Patient.query.filter_by(id=patient_id, deleted=False).first()
    if not patient or patient.therapist_id != current_user.id:
        abort(403)
    return patient


@app.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = 10

    patients = Patient.active().filter_by(therapist_id=current_user.id).all()

    # Get paginated calls
    calls_query = Call.query.filter(
        Call.patient_phone.in_([p.phone for p in patients])
    ).order_by(Call.created_at.desc())

    calls_pagination = calls_query.paginate(page=page, per_page=per_page, error_out=False)
    recent_calls = calls_pagination.items

    stats = {
        'total_patients': len(patients),
        'recent_calls': calls_query.count(),
        'batches': len(set(p.batch for p in patients if p.batch))
    }

    return render_template('dashboard.html', patients=patients,
                         recent_calls=recent_calls, stats=stats,
                         pagination=calls_pagination)


@app.route('/patients')
@login_required
def patient_list():
    patients = Patient.active().filter_by(therapist_id=current_user.id).all()
    return render_template('patient_list.html', patients=patients)


@app.route('/patients/new', methods=['GET', 'POST'])
@login_required
def patient_new():
    if request.method == 'POST':
        phone = request.form.get('phone')
        name = request.form.get('name')
        nickname = request.form.get('nickname')
        batch = request.form.get('batch')
        language = request.form.get('language', 'he-IL')

        if Patient.query.filter_by(phone=phone).first():
            flash('Patient with this phone already exists', 'error')
        else:
            patient = Patient(
                name=name,
                phone=phone,
                nickname=nickname,
                batch=batch,
                language=language,
                therapist_id=current_user.id
            )
            db.session.add(patient)
            db.session.commit()
            flash(f'Patient {name} created successfully', 'success')
            return redirect(url_for('patient_list'))

    return render_template('patient_form.html', patient=None)


@app.route('/patients/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def patient_edit(id):
    patient = get_patient_or_403(id)

    if request.method == 'POST':
        patient.name = request.form.get('name')
        patient.nickname = request.form.get('nickname')
        patient.phone = request.form.get('phone')
        patient.batch = request.form.get('batch')
        patient.language = request.form.get('language')
        db.session.commit()
        flash(f'Patient {patient.name} updated successfully', 'success')
        return redirect(url_for('patient_list'))

    return render_template('patient_form.html', patient=patient)


@app.route('/patients/<int:id>/delete', methods=['POST'])
@login_required
def patient_delete(id):
    patient = get_patient_or_403(id)
    patient.deleted = True
    db.session.commit()
    flash(f'Patient {patient.name} deleted successfully', 'success')
    return redirect(url_for('patient_list'))


@app.route('/patients/<int:id>/call', methods=['POST'])
@login_required
def patient_call(id):
    patient = get_patient_or_403(id)

    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
    client = Client(account_sid, auth_token)

    TWILIO_NUMBER = "+17473023043"
    NGROK_URL = "http://188.166.110.236:5000"

    try:
        call = client.calls.create(
            to=patient.phone,
            from_=TWILIO_NUMBER,
            url=f'{NGROK_URL}/voice?lang={patient.language}&name={quote(patient.nickname or patient.name)}&batch={patient.batch or "basic"}&questionId=1&to_phone={quote(patient.phone)}&from_phone={quote(TWILIO_NUMBER)}',
            record=True,
            recording_channels='dual'
        )

        flash(f'Call initiated to {patient.name}. Call SID: {call.sid}', 'success')
    except Exception as e:
        flash(f'Call failed: {str(e)}', 'error')

    return redirect(url_for('dashboard'))
