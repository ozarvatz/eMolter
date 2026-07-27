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
import scipy.stats as stats

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


def _extract_length(pr):
    if not isinstance(pr, dict):
        return None
    v = _safe_float(pr.get('total_duration'))
    if v is not None:
        return v
    vqs = pr.get('voice_quality_stats') or {}
    return _safe_float(vqs.get('from_0_to_0_seconds_duration'))


def _extract_net_talk(pr):
    """Net patient speaking time (s) = total_duration * speaking_ratio.
    speaking_ratio is the fraction of the recording that is actual voiced
    speech (0..1), so the product is how long the patient actually talked.
    This is the prosody-quality signal: clinical voice metrics need ~30-90s
    of net speech to be reliable."""
    if not isinstance(pr, dict):
        return None
    total = _extract_length(pr)
    ratio = _safe_float(pr.get('speaking_ratio'))
    if total is None or ratio is None:
        return None
    return total * ratio


def _extract_v(pr, index):
    if not isinstance(pr, dict): return None
    stats = pr.get('stats')
    if not stats: return None
    v = stats.get('v')
    if not isinstance(v, list) or len(v) <= index: return None
    return _safe_float(v[index])


def _extract_s_scalar(pr):
    if not isinstance(pr, dict): return None
    stats = pr.get('stats')
    if not stats: return None
    return _safe_float(stats.get('s'))


METRICS = [
    ('pause_duration_v',    'Pause Duration (v)',    lambda pr: _extract_v(pr, 0)),
    ('spiking_rate_v',      'Spiking Rate (v)',      lambda pr: _extract_v(pr, 1)),
    ('f0_variability_hz_v', 'F0 Variability (v)',    lambda pr: _extract_v(pr, 2)),
    ('jitter_local_v',      'Jitter Local (v)',      lambda pr: _extract_v(pr, 3)),
    ('cpp_v',               'CPP (v)',               lambda pr: _extract_v(pr, 4)),
    ('s_scalar',            'S Scalar',              _extract_s_scalar),
]


# --- Helpers -----------------------------------------------------------------

def mann_kendall_test(x, indices=None, alpha=0.05):
    """
    Simple Mann-Kendall trend test.
    Returns: (trend, p_value, slope, intercept)
    """
    import numpy as np
    import math
    
    def normal_cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    n = len(x)
    if n < 3:
        return 'no trend', 1.0, 0.0, np.mean(x) if n > 0 else 0.0
    
    if indices is None:
        indices = list(range(n))

    s = 0
    for k in range(n - 1):
        for j in range(k + 1, n):
            s += np.sign(x[j] - x[k])
            
    unique_x = np.unique(x)
    g = len(unique_x)
    if n == g:
        var_s = (n * (n - 1) * (2 * n + 5)) / 18
    else:
        _, tp = np.unique(x, return_counts=True)
        var_s = (n * (n - 1) * (2 * n + 5) - np.sum(tp * (tp - 1) * (2 * tp + 5))) / 18
        
    if s > 0:
        z = (s - 1) / np.sqrt(var_s) if var_s > 0 else 0
    elif s < 0:
        z = (s + 1) / np.sqrt(var_s) if var_s > 0 else 0
    else:
        z = 0
        
    p = 2 * (1 - normal_cdf(abs(z)))
    
    if p < alpha:
        trend = 'increasing' if s > 0 else 'decreasing'
    else:
        trend = 'no trend'
        
    slopes = []
    for k in range(n - 1):
        for j in range(k + 1, n):
            dx = indices[j] - indices[k]
            if dx != 0:
                slopes.append((x[j] - x[k]) / dx)
    
    slope = float(np.median(slopes)) if slopes else 0.0
    intercept = float(np.median(x) - slope * np.median(indices))
    
    return trend, float(p), slope, intercept

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
        window = int(request.args.get('window', 10))
    except (TypeError, ValueError):
        window = 10
    window = max(2, min(window, 1000))

    try:
        threshold_k = float(request.args.get('threshold', 2.0))
    except (TypeError, ValueError):
        threshold_k = 2.0
    threshold_k = max(0.5, min(threshold_k, 5.0))

    try:
        visible = int(request.args.get('visible', 14))
    except (TypeError, ValueError):
        visible = 14
    visible = max(2, min(visible, 1000))

    try:
        net_talk_threshold = float(request.args.get('net_talk_threshold', 30.0))
    except (TypeError, ValueError):
        net_talk_threshold = 30.0

    try:
        p_alpha = float(request.args.get('p_value_threshold', 0.05))
    except (TypeError, ValueError):
        p_alpha = 0.05

    calls = (
        Call.query
        .filter(Call.patient_phone == patient.phone)
        .filter(Call.is_processed == True)  # noqa: E712
        .filter(Call.prosody_results.isnot(None))
        .filter(Call.recording_url.isnot(None))
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
    all_net_talk = [_extract_net_talk(c.prosody_results) for c in calls]
    all_patient_topics = [(c.topics.get('patient_topics', []) if c.topics else []) for c in calls]

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
        net_talk_slice = all_net_talk[start:]

        # MK Trend calculation on the VISIBLE slice, considering net_talk_threshold
        trend_input = []
        trend_indices = []
        for i, (v, nt) in enumerate(zip(values, net_talk_slice)):
            if v is not None and nt is not None and nt >= net_talk_threshold:
                trend_input.append(v)
                trend_indices.append(i)
        
        trend_res, p_val, slope, intercept = mann_kendall_test(trend_input, trend_indices, alpha=p_alpha)

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
            'trend': trend_res,
            'p_value': p_val,
            'slope': slope,
            'intercept': intercept
        }

    # --- Population Comparison: Multivariate Outlier Detection ---
    # As requested by the analytics team, this replaces the 1D Welch's t-test on S-scalars.
    # Instead of comparing means, we treat each patient call as a point in a 5D feature space
    # (Pause, Spiking, F0, Jitter, CPP). We compute the Mahalanobis Distance (D^2) of the 
    # target patient's centroid against the global population's covariance matrix.
    # 
    # Mahalanobis distance acts as a correlation-aware, multivariate generalization of a z-score.
    # Assuming the population cloud is roughly multivariate normal, D^2 follows a Chi-squared 
    # distribution with k=5 degrees of freedom. We use the Chi-squared CDF to compute the exact 
    # probability (p-value) of observing a point at least this extreme by chance.
    
    import numpy as np

    def _extract_vector(pr):
        if not isinstance(pr, dict): return None
        stats_dict = pr.get('stats')
        if not stats_dict: return None
        v = stats_dict.get('v')
        if not isinstance(v, list) or len(v) != 5: return None
        return v

    # 1. Extract valid 5D vectors for the target patient
    target_vectors = []
    for pr, nt in zip((c.prosody_results for c in calls[start:]), net_talk_slice):
        if pr is not None and nt is not None and nt >= net_talk_threshold:
            vec = _extract_vector(pr)
            if vec is not None:
                target_vectors.append(vec)

    # 2. Efficiently fetch ALL other prosody results to check net_talk and extract 5D vectors
    other_results = Call.query.with_entities(Call.prosody_results).filter(
        Call.patient_phone != patient.phone,
        Call.is_processed == True,
        Call.prosody_results.isnot(None)
    ).all()

    other_vectors = []
    for (pr,) in other_results:
        nt = _extract_net_talk(pr)
        if nt is not None and nt >= net_talk_threshold:
            vec = _extract_vector(pr)
            if vec is not None:
                other_vectors.append(vec)

    s_p_value = None
    if len(target_vectors) >= 1 and len(other_vectors) > 5:
        try:
            # Calculate centroids and covariance
            target_centroid = np.mean(target_vectors, axis=0)
            pop_centroid = np.mean(other_vectors, axis=0)
            pop_cov = np.cov(other_vectors, rowvar=False)

            # Mahalanobis distance squared (D^2 = (x - u)' * inv(Cov) * (x - u))
            inv_cov = np.linalg.pinv(pop_cov)
            diff = target_centroid - pop_centroid
            d_squared = np.dot(np.dot(diff, inv_cov), diff)

            # Convert to p-value using Chi-Squared CDF (5 degrees of freedom for 5D vector)
            s_p_value = float(1.0 - stats.chi2.cdf(d_squared, 5))
        except Exception:
            s_p_value = None

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
        'net_talk_threshold': net_talk_threshold,
        'p_value_threshold': p_alpha,
        'n_calls': n_total,
        'n_visible': n_show,
        'dates':      all_dates[start:],
        'call_ids':   all_call_ids[start:],
        'treatments': all_treatments[start:],
        'utms':       all_utms[start:],
        'genders':    all_genders[start:],
        'ages':       all_ages[start:],
        'contexts':   all_contexts[start:],
        'patient_topics': all_patient_topics[start:],
        'metrics': metrics,
        's_scalar_p_value': float(s_p_value) if s_p_value is not None else None,
        's_scalar_status': 'DIFFERENT' if (s_p_value is not None and s_p_value < 0.05) else 'Normal'
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
