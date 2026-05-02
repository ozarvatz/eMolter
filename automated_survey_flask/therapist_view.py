import io
import json
import pandas as pd
from flask import render_template, request, redirect, url_for, flash, abort, make_response
from flask_login import login_required, current_user
from automated_survey_flask import app, db
from automated_survey_flask.models import Patient, Call, ProsodyParameter, QuestionSet
from twilio.rest import Client
import os
from urllib.parse import quote


def _available_batches():
    """Return sorted list of distinct batch names the current user owns (or all for superuser)."""
    q = db.session.query(QuestionSet.batch).distinct()
    if not current_user.is_superuser:
        q = q.filter(QuestionSet.created_by_id == current_user.id)
    return [r[0] for r in q.order_by(QuestionSet.batch).all()]


def get_patient_or_403(patient_id):
    """Get patient and verify ownership (superusers bypass ownership check)"""
    patient = Patient.query.filter_by(id=patient_id, deleted=False).first()
    if not patient or (not current_user.is_superuser and patient.therapist_id != current_user.id):
        abort(403)
    return patient


@app.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = 10

    if current_user.is_superuser:
        patients = Patient.active().all()
    else:
        patients = Patient.active().filter_by(therapist_id=current_user.id).all()

    # Create phone to patient mapping for displaying nicknames
    phone_to_patient = {p.phone: p for p in patients}

    # Get prosody parameter explanations
    prosody_params = {p.parameter_key: p for p in ProsodyParameter.query.all()}

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
                         pagination=calls_pagination,
                         phone_to_patient=phone_to_patient,
                         prosody_params=prosody_params)


@app.route('/patients')
@login_required
def patient_list():
    if current_user.is_superuser:
        patients = Patient.active().all()
    else:
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

    return render_template('patient_form.html', patient=None, batches=_available_batches())


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

    return render_template('patient_form.html', patient=patient, batches=_available_batches())


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
    NGROK_URL = "https://www.emolter.org:5000"

    try:
        call = client.calls.create(
            to=patient.phone,
            from_=TWILIO_NUMBER,
            url=f'{NGROK_URL}/voice?lang={patient.language}&name={quote(patient.nickname or patient.name)}&batch={patient.batch or "basic"}&questionId=1&to_phone={quote(patient.phone)}&from_phone={quote(TWILIO_NUMBER)}',
            record=True,
            recording_channels='dual',
            recording_status_callback=f'{NGROK_URL}/non-llm-recording-callback',
            recording_status_callback_method='POST',
        )

        flash(f'Call initiated to {patient.name}. Call SID: {call.sid}', 'success')
    except Exception as e:
        flash(f'Call failed: {str(e)}', 'error')

    return redirect(url_for('dashboard'))


@app.route('/export-csv')
@login_required
def export_csv():
    if current_user.is_superuser:
        query = db.session.query(Call)
    else:
        patients = Patient.active().filter_by(therapist_id=current_user.id).all()
        phones = [p.phone for p in patients]
        query = db.session.query(Call).filter(Call.patient_phone.in_(phones))

    df = pd.read_sql(query.statement, db.engine)

    if df.empty:
        flash('No data available to export.', 'warning')
        return redirect(url_for('dashboard'))

    json_struct = df['prosody_results'].apply(
        lambda x: x if isinstance(x, dict) else json.loads(x or '{}')
    )
    df_json = pd.json_normalize(json_struct)
    df_final = pd.concat([df.drop('prosody_results', axis=1), df_json], axis=1)

    output = io.StringIO()
    df_final.to_csv(output, index=False)
    output.seek(0)

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=prosody_report.csv'
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    return response
