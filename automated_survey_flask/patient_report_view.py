"""Per-patient 30-second prosody report.

Three metrics — mean pitch (Hz), call length (s), and degree of voice breaks (%)
— plotted over time with a configurable rolling baseline (window of last N calls)
and a ±k·STD threshold band. Line segments change color when the patient's
treatment, UTM source, or gender changes between consecutive calls; markers turn
red when the value falls outside the band. Hover shows date, value, treatment,
UTM, gender, and age at the time of the call.
"""
from datetime import datetime

from flask import render_template, jsonify, abort, request
from flask_login import login_required, current_user

from automated_survey_flask import app, csrf
from automated_survey_flask.models import Patient, Call


# --- Metric extractors -------------------------------------------------------
# For each chart we accept the top-level value and fall back to the
# voice_quality_stats block where applicable (older records).

def _safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or f == float('inf') or f == float('-inf'):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _extract_pitch(pr):
    return _safe_float(pr.get('mean_pitch_hz')) if isinstance(pr, dict) else None


def _extract_length(pr):
    if not isinstance(pr, dict):
        return None
    v = _safe_float(pr.get('total_duration'))
    if v is not None:
        return v
    vqs = pr.get('voice_quality_stats') or {}
    return _safe_float(vqs.get('from_0_to_0_seconds_duration'))


def _extract_voice_breaks(pr):
    if not isinstance(pr, dict):
        return None
    vqs = pr.get('voice_quality_stats') or {}
    return _safe_float(vqs.get('degree_of_voice_breaks'))


METRICS = [
    ('pitch',        'Mean Pitch (Hz)',       _extract_pitch),
    ('length',       'Call Length (s)',       _extract_length),
    ('voice_breaks', 'Voice Breaks (%)',      _extract_voice_breaks),
]


# --- Helpers -----------------------------------------------------------------

def _patient_for(patient_id):
    """Look up the patient and enforce ownership: superuser sees all, therapists
    only their own."""
    patient = Patient.query.filter_by(id=patient_id, deleted=False).first()
    if not patient:
        abort(404)
    if not current_user.is_superuser and patient.therapist_id != current_user.id:
        abort(403)
    return patient


def _utm_source(utm_params):
    """Pick a single short UTM identifier — utm_source is the convention. Fall back
    to first non-empty key so we still see *something* when only campaign/medium
    is set."""
    if not isinstance(utm_params, dict):
        return None
    for key in ('utm_source', 'utm_campaign', 'utm_medium', 'utm_term', 'utm_content'):
        v = utm_params.get(key)
        if v:
            return str(v)
    return None


def _age_at(birth_year, when):
    if not birth_year or not when:
        return None
    try:
        return int(when.year) - int(birth_year)
    except (TypeError, ValueError):
        return None


def _baseline_stats(values, window):
    """Mean and population stddev over the last `window` non-null values. Returns
    (mean, std, n) or (None, None, 0) when there aren't enough numeric points."""
    nums = [v for v in values if v is not None]
    if not nums:
        return None, None, 0
    sample = nums[-window:] if window > 0 else nums
    n = len(sample)
    if n == 0:
        return None, None, 0
    mean = sum(sample) / n
    if n < 2:
        return mean, 0.0, n
    var = sum((x - mean) ** 2 for x in sample) / n
    return mean, var ** 0.5, n


# --- Routes ------------------------------------------------------------------

@app.route('/therapist/patients/<int:patient_id>/report')
@login_required
def patient_report(patient_id):
    patient = _patient_for(patient_id)
    return render_template('patient_report.html', patient=patient)


@app.route('/api/therapist/patients/<int:patient_id>/report/data')
@csrf.exempt
@login_required
def patient_report_data(patient_id):
    patient = _patient_for(patient_id)

    # Slider params with safe defaults / clamps.
    try:
        window = int(request.args.get('window', 30))
    except (TypeError, ValueError):
        window = 30
    window = max(2, min(window, 1000))

    try:
        threshold_k = float(request.args.get('threshold', 2.0))
    except (TypeError, ValueError):
        threshold_k = 2.0
    threshold_k = max(0.5, min(threshold_k, 5.0))

    calls = (
        Call.query
        .filter(Call.patient_phone == patient.phone)
        .filter(Call.is_processed == True)  # noqa: E712
        .filter(Call.prosody_results.isnot(None))
        .order_by(Call.created_at.asc())
        .all()
    )

    dates = [c.created_at.strftime('%Y-%m-%d %H:%M') if c.created_at else None for c in calls]
    treatments = [c.patient_treatment for c in calls]
    utms = [_utm_source(c.patient_utm_params) for c in calls]
    genders = [c.patient_gender for c in calls]
    ages = [_age_at(c.patient_birth_year, c.created_at) for c in calls]

    # A context "key" per call — when this changes between consecutive calls,
    # the line segment is drawn in a new color.
    contexts = [
        f"{t or '∅'} | {u or '∅'} | {g or '∅'}"
        for t, u, g in zip(treatments, utms, genders)
    ]

    metrics = {}
    for key, label, extractor in METRICS:
        values = [extractor(c.prosody_results) for c in calls]
        mean, std, n = _baseline_stats(values, window)
        if mean is None or std is None:
            low = high = None
        else:
            low = mean - threshold_k * std
            high = mean + threshold_k * std
        out_of_band = [
            (v is not None and low is not None and (v < low or v > high))
            for v in values
        ]
        metrics[key] = {
            'label': label,
            'values': values,
            'mean': mean,
            'std': std,
            'baseline_n': n,
            'threshold_low': low,
            'threshold_high': high,
            'out_of_band': out_of_band,
            'out_of_band_count': sum(1 for x in out_of_band if x),
        }

    return jsonify({
        'patient': {
            'name': patient.nickname or patient.name,
            'phone': patient.phone,
            'gender': patient.gender,
            'birth_year': patient.birth_year,
            'current_age': _age_at(patient.birth_year, datetime.now()),
            'treatment': patient.treatment,
            'utm_source': _utm_source(patient.utm_params),
        },
        'window': window,
        'threshold_k': threshold_k,
        'n_calls': len(calls),
        'dates': dates,
        'treatments': treatments,
        'utms': utms,
        'genders': genders,
        'ages': ages,
        'contexts': contexts,
        'metrics': metrics,
    })
