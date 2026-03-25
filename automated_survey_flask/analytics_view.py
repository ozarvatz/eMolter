from flask import render_template, request, jsonify
from flask_login import login_required, current_user
from automated_survey_flask import app, db, csrf
from automated_survey_flask.models import Patient, Call
from datetime import datetime, timedelta
from sqlalchemy import and_


@app.route('/analytics')
@login_required
def analytics():
    """Analytics dashboard with time-series graphs"""
    # Get patients based on user role
    if current_user.is_superuser:
        patients = Patient.active().all()
    else:
        patients = Patient.active().filter_by(therapist_id=current_user.id).all()

    return render_template('analytics.html', patients=patients)


@app.route('/api/analytics/data')
@csrf.exempt
def analytics_data():
    """API endpoint to get time-series data for graphs"""

    # Check authentication for AJAX - return JSON error instead of redirect
    if not current_user.is_authenticated:
        return jsonify({'error': 'Authentication required'}), 401

    try:
        # Get filter parameters
        patient_ids = request.args.getlist('patient_ids[]', type=int)
        days = request.args.get('days', 30, type=int)

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # Get patients based on user role
        if current_user.is_superuser:
            patients_query = Patient.active()
        else:
            patients_query = Patient.active().filter_by(therapist_id=current_user.id)

        # Filter by selected patients if provided
        if patient_ids:
            patients_query = patients_query.filter(Patient.id.in_(patient_ids))

        patients = patients_query.all()

        if not patients:
            return jsonify({})

        patient_phones = [p.phone for p in patients]

        # Query calls within date range
        calls = Call.query.filter(
            and_(
                Call.patient_phone.in_(patient_phones),
                Call.created_at >= start_date,
                Call.created_at <= end_date,
                Call.is_processed == True,
                Call.prosody_results.isnot(None)
            )
        ).order_by(Call.created_at.asc()).all()

        # Create phone to patient mapping
        phone_to_patient = {p.phone: p for p in patients}

        # Organize data by patient
        data_by_patient = {}

        for call in calls:
            patient = phone_to_patient.get(call.patient_phone)
            if not patient:
                continue

            patient_name = patient.nickname or patient.name

            if patient_name not in data_by_patient:
                data_by_patient[patient_name] = {
                    'dates': [],
                    'pitch_mean': [],
                    'pitch_sd': [],
                    'shimmer': [],
                    'jitter': [],
                    'hnr': [],
                    'voice_breaks': []
                }

            # Extract prosody metrics
            pr = call.prosody_results
            vqs = pr.get('voice_quality_stats', {}) if isinstance(pr, dict) else {}

            data_by_patient[patient_name]['dates'].append(
                call.created_at.strftime('%Y-%m-%d %H:%M')
            )

            # Extract metrics with safe defaults - convert to float or None
            def safe_float(value):
                """Convert value to float or None, handling NaN"""
                if value is None:
                    return None
                try:
                    f = float(value)
                    # Check for NaN and Infinity - return None for invalid values
                    if f != f or f == float('inf') or f == float('-inf'):
                        return None
                    return f
                except (TypeError, ValueError):
                    return None

            data_by_patient[patient_name]['pitch_mean'].append(
                safe_float(pr.get('mean_pitch_hz')) if isinstance(pr, dict) else None
            )
            data_by_patient[patient_name]['pitch_sd'].append(
                safe_float(vqs.get('standard_deviation')) if vqs else None
            )
            data_by_patient[patient_name]['shimmer'].append(
                safe_float(vqs.get('shimmer_local')) if vqs else None
            )
            data_by_patient[patient_name]['jitter'].append(
                safe_float(vqs.get('jitter_local')) if vqs else None
            )
            data_by_patient[patient_name]['hnr'].append(
                safe_float(pr.get('mean_hnr_db')) if isinstance(pr, dict) else None
            )
            data_by_patient[patient_name]['voice_breaks'].append(
                safe_float(vqs.get('degree_of_voice_breaks')) if vqs else None
            )

        return jsonify(data_by_patient)

    except Exception as e:
        # Log the error and return proper JSON error response
        app.logger.error(f"Analytics data error: {str(e)}")
        return jsonify({'error': str(e)}), 500
