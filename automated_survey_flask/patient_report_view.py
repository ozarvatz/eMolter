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

    # `visible` clips the chart to the most recent N calls. Baseline is still
    # computed from up to `window` of the FULL history so the band reflects
    # the patient's true normal, not just what's currently on screen.
    try:
        visible = int(request.args.get('visible', 14))
    except (TypeError, ValueError):
        visible = 14
    visible = max(2, min(visible, 1000))

    calls = (
        Call.query
        .filter(Call.patient_phone == patient.phone)
        .filter(Call.is_processed == True)  # noqa: E712
        .filter(Call.prosody_results.isnot(None))
        .order_by(Call.created_at.asc())
        .all()
    )

    # Full-history per-call arrays (used for baseline)
    all_dates = [c.created_at.strftime('%Y-%m-%d %H:%M') if c.created_at else None for c in calls]
    all_call_ids = [c.id for c in calls]
    all_treatments = [c.patient_treatment for c in calls]
    all_utms = [_utm_source(c.patient_utm_params) for c in calls]
    all_genders = [c.patient_gender for c in calls]
    all_ages = [_age_at(c.patient_birth_year, c.created_at) for c in calls]
    all_contexts = [
        f"{t or '∅'} | {u or '∅'} | {g or '∅'}"
        for t, u, g in zip(all_treatments, all_utms, all_genders)
    ]

    # Decide the display slice — the last `visible` calls.
    n_total = len(calls)
    n_show = min(visible, n_total)
    start = n_total - n_show

    metrics = {}
    for key, label, extractor in METRICS:
        all_values = [extractor(c.prosody_results) for c in calls]
        mean, std, n = _baseline_stats(all_values, window)  # baseline from full history
        if mean is None or std is None:
            low = high = None
        else:
            low = mean - threshold_k * std
            high = mean + threshold_k * std
        all_out_of_band = [
            (v is not None and low is not None and (v < low or v > high))
            for v in all_values
        ]
        # Sliced to display range
        values = all_values[start:]
        out_of_band = all_out_of_band[start:]
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
        'visible': visible,
        'n_calls': n_total,
        'n_visible': n_show,
        'dates':      all_dates[start:],
        'call_ids':   all_call_ids[start:],
        'treatments': all_treatments[start:],
        'utms':       all_utms[start:],
        'genders':    all_genders[start:],
        'ages':       all_ages[start:],
        'contexts':   all_contexts[start:],
        'metrics': metrics,
    })


@app.route('/api/therapist/calls/<int:call_id>/conversation')
@csrf.exempt
@login_required
def call_conversation_data(call_id):
    """Return one call's conversation + topics for the modal viewer.
    Auth: patient must belong to current therapist (or superuser)."""
    import json as _json
    call = Call.query.get(call_id)
    if not call:
        abort(404)

    # Authorize: find the patient row by phone and check ownership.
    patient = (Patient.query
               .filter_by(phone=call.patient_phone, deleted=False)
               .first())
    if patient and not current_user.is_superuser and patient.therapist_id != current_user.id:
        abort(403)

    # Parse conversation_text into a list of {q, a} dicts. Two formats:
    #   - LLM flow: JSON array string [{"q":"...","a":"..."}]
    #   - non-LLM:  "Q1: ...\nA1: ...\nQ2: ..." text
    turns = []
    text = (call.conversation_text or '').strip()
    parsed_json = False
    if text:
        try:
            data = _json.loads(text)
            if isinstance(data, list):
                turns = [{'q': (t.get('q') or '').strip(),
                          'a': (t.get('a') or '').strip()} for t in data]
                parsed_json = True
        except (ValueError, TypeError):
            pass
        if not parsed_json:
            # Best-effort parse of "Qn: ...\nAn: ..." pairs.
            import re as _re
            current_q = None
            for line in text.splitlines():
                m_q = _re.match(r'^\s*Q\d*\s*:\s*(.*)', line)
                m_a = _re.match(r'^\s*A\d*\s*:\s*(.*)', line)
                if m_q:
                    current_q = m_q.group(1).strip()
                    turns.append({'q': current_q, 'a': ''})
                elif m_a and turns:
                    turns[-1]['a'] = m_a.group(1).strip()
                elif turns and not m_q:
                    # Continuation line — append to last segment.
                    if turns[-1]['a'] == '':
                        turns[-1]['q'] += (' ' + line.strip()) if line.strip() else ''
                    else:
                        turns[-1]['a'] += (' ' + line.strip()) if line.strip() else ''
            if not turns and text:
                turns = [{'q': '', 'a': text}]

    topics = call.topics if isinstance(call.topics, dict) else {}

    return jsonify({
        'call_id':     call.id,
        'call_sid':    call.call_sid,
        'created_at':  call.created_at.strftime('%Y-%m-%d %H:%M') if call.created_at else None,
        'patient_phone': call.patient_phone,
        'turns':       turns,
        'topics': {
            'bot_topics':     topics.get('bot_topics', []) or [],
            'patient_topics': topics.get('patient_topics', []) or [],
        },
    })
