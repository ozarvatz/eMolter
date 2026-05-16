import io
import json
import pandas as pd
from flask import render_template, request, redirect, url_for, flash, abort, make_response
from flask_login import login_required, current_user
from automated_survey_flask import app, db
from automated_survey_flask.models import Patient, Call, ProsodyParameter, QuestionSet, CallSchedule
from twilio.rest import Client
import os
from urllib.parse import quote


FREQUENCY_CHOICES = ['daily', 'weekly', 'biweekly']
GENDER_CHOICES    = ['male', 'female', 'other']


def _parse_utm_string(raw):
    """Parse "key1=val1,key2=val2" into {"key1": "val1", "key2": "val2"}.
    Entries without "=" or with empty key are skipped."""
    out = {}
    for pair in (raw or '').split(','):
        pair = pair.strip()
        if '=' not in pair:
            continue
        k, v = pair.split('=', 1)
        k, v = k.strip(), v.strip()
        if k:
            out[k] = v
    return out


def _format_utm_dict(d):
    """Inverse of _parse_utm_string — used to pre-fill the edit form."""
    if not d:
        return ''
    return ','.join(f'{k}={v}' for k, v in d.items())


def _available_batches():
    """Return sorted list of distinct batch names the current user owns (or all for superuser)."""
    q = db.session.query(QuestionSet.batch).distinct()
    if not current_user.is_superuser:
        q = q.filter(QuestionSet.created_by_id == current_user.id)
    return [r[0] for r in q.order_by(QuestionSet.batch).all()]


def _treatment_suggestions():
    """Distinct treatment values across all patients, with the current user's patients first."""
    own_q = (
        db.session.query(Patient.treatment)
        .filter(Patient.treatment.isnot(None), Patient.treatment != '', Patient.deleted == False)
        .filter(Patient.therapist_id == current_user.id)
        .distinct()
    )
    own = [r[0] for r in own_q.all()]

    other_q = (
        db.session.query(Patient.treatment)
        .filter(Patient.treatment.isnot(None), Patient.treatment != '', Patient.deleted == False)
        .filter(Patient.therapist_id != current_user.id)
        .distinct()
    )
    others = [r[0] for r in other_q.all()]

    # Keep `own` order, then append `others` not already present.
    seen = set(own)
    merged = list(own)
    for t in others:
        if t not in seen:
            merged.append(t)
            seen.add(t)
    return merged


def _parse_patient_form():
    """Pull and normalise the patient fields from the request form."""
    birth_year_raw = request.form.get('birth_year', '').strip()
    birth_year = int(birth_year_raw) if birth_year_raw.isdigit() else None

    utm = _parse_utm_string(request.form.get('utm_params', ''))

    return {
        'name':       request.form.get('name', '').strip(),
        'nickname':   request.form.get('nickname', '').strip() or None,
        'phone':      request.form.get('phone', '').strip(),
        'batch':      request.form.get('batch', '').strip() or None,
        'language':   request.form.get('language', 'he-IL'),
        'gender':     request.form.get('gender', '').strip() or None,
        'birth_year': birth_year,
        'treatment':  request.form.get('treatment', '').strip() or None,
        'utm_params': utm or None,
    }


def snapshot_patient_to_call(call_record, patient):
    """Copy patient demographic+context fields onto a Call so the export reflects the values
    that were true when the call was placed (not the current values in the patients row).
    Caller commits."""
    call_record.patient_gender     = patient.gender
    call_record.patient_birth_year = patient.birth_year
    call_record.patient_treatment  = patient.treatment
    call_record.patient_utm_params = patient.utm_params


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
        data = _parse_patient_form()

        if Patient.query.filter_by(phone=data['phone']).first():
            flash('Patient with this phone already exists', 'error')
        else:
            patient = Patient(therapist_id=current_user.id, **data)
            db.session.add(patient)
            db.session.commit()
            flash(f'Patient {patient.name} created successfully', 'success')
            return redirect(url_for('patient_list'))

    return render_template(
        'patient_form.html',
        patient=None,
        batches=_available_batches(),
        treatment_suggestions=_treatment_suggestions(),
        gender_choices=GENDER_CHOICES,
        utm_params_str='',
        frequency_choices=FREQUENCY_CHOICES,
    )


@app.route('/patients/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def patient_edit(id):
    patient = get_patient_or_403(id)

    if request.method == 'POST':
        data = _parse_patient_form()
        for field, value in data.items():
            setattr(patient, field, value)
        db.session.commit()
        flash(f'Patient {patient.name} updated successfully', 'success')
        return redirect(url_for('patient_list'))

    return render_template(
        'patient_form.html',
        patient=patient,
        batches=_available_batches(),
        treatment_suggestions=_treatment_suggestions(),
        gender_choices=GENDER_CHOICES,
        utm_params_str=_format_utm_dict(patient.utm_params) if patient else '',
        frequency_choices=FREQUENCY_CHOICES,
    )


@app.route('/patients/<int:id>/schedule', methods=['POST'])
@login_required
def patient_schedule_save(id):
    """Create or update the recurring-call schedule for a patient."""
    patient = get_patient_or_403(id)

    frequency = request.form.get('frequency', '').strip()
    hour_raw  = request.form.get('hour', '').strip()
    active    = request.form.get('schedule_active') == 'on'

    if frequency not in FREQUENCY_CHOICES or not hour_raw.isdigit():
        flash('Invalid schedule: frequency and hour are required.', 'error')
        return redirect(url_for('patient_edit', id=id))

    hour = int(hour_raw)
    if not (0 <= hour <= 23):
        flash('Hour must be between 0 and 23.', 'error')
        return redirect(url_for('patient_edit', id=id))

    schedule = CallSchedule.query.filter_by(patient_id=patient.id).first()
    if not schedule:
        schedule = CallSchedule(patient_id=patient.id, frequency=frequency, hour=hour, active=active)
        db.session.add(schedule)
    else:
        schedule.frequency = frequency
        schedule.hour      = hour
        schedule.active    = active

    db.session.commit()
    flash(f'Schedule saved for {patient.name}.', 'success')
    return redirect(url_for('patient_edit', id=id))


@app.route('/patients/<int:id>/schedule/delete', methods=['POST'])
@login_required
def patient_schedule_delete(id):
    patient = get_patient_or_403(id)
    schedule = CallSchedule.query.filter_by(patient_id=patient.id).first()
    if schedule:
        db.session.delete(schedule)
        db.session.commit()
        flash('Schedule removed.', 'success')
    return redirect(url_for('patient_edit', id=id))


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

    if 'patient_utm_params' in df_final.columns:
        df_final['patient_utm_params'] = df_final['patient_utm_params'].apply(
            lambda x: _format_utm_dict(x if isinstance(x, dict) else (json.loads(x) if x else {}))
        )

    output = io.StringIO()
    df_final.to_csv(output, index=False)
    output.seek(0)

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=prosody_report.csv'
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    return response
