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

    # 1. Fetch Target Patient Calls
    patient_calls = (
        Call.query
        .filter(Call.patient_phone == patient.phone)
        .filter(Call.is_processed == True)  # noqa: E712
        .filter(Call.prosody_results.isnot(None))
        .filter(Call.recording_url.isnot(None))
        .order_by(Call.created_at.asc())
        .all()
    )

    import numpy as np
    from sklearn.decomposition import PCA

    def _extract_5d_vector(pr):
        if not isinstance(pr, dict): return None
        
        # We need the base metrics: pause, spiking, f0, jitter, cpp
        # Fallback to the 'v' array in 'stats' if pre-computed, but since we are doing
        # raw un-normalized values, it's safer to extract the raw values directly.
        # However, the user's `prosody.py` calculates them as `x1..x5`.
        # For this prototype, we'll extract the raw values assuming they exist,
        # or fallback to the pre-computed 'v' vector from the dictionary.
        
        stats_dict = pr.get('stats')
        if not stats_dict: return None
        
        # The user's prosody.py calculates 'v' as normalized. We need raw to calculate mean.
        # But wait, the user's spec says "find the mean of each parameter".
        # Let's extract the raw features from `prosody_results`.
        
        pause = _safe_float(pr.get('pause_duration'))
        spiking = _safe_float(pr.get('spiking_rate'))
        f0 = _safe_float(pr.get('f0_variability_hz'))
        vqs = pr.get('voice_quality_stats', {})
        jitter = _safe_float(vqs.get('jitter_local'))
        cpp = _safe_float(pr.get('cpp'))
        
        # If raw aren't available in top level (legacy calls), try to fallback 
        # to the 'dictionary' mapping in 'stats' if it existed, but it's risky.
        # We will require all 5 raw metrics to be present.
        if None in (pause, spiking, f0, jitter, cpp):
            return None
            
        return [pause, spiking, f0, jitter, cpp]

    # --- Step A: Build the Global Centered Cloud ---
    # Fetch ALL processed calls from ALL patients
    all_calls = Call.query.filter(
        Call.is_processed == True,
        Call.prosody_results.isnot(None)
    ).all()

    # Group by patient phone
    calls_by_patient = {}
    for c in all_calls:
        nt = _extract_net_talk(c.prosody_results)
        if nt is not None and nt >= net_talk_threshold:
            vec = _extract_5d_vector(c.prosody_results)
            if vec is not None:
                phone = c.patient_phone
                if phone not in calls_by_patient:
                    calls_by_patient[phone] = []
                # Also store the call ID so we can match it back for the target patient
                calls_by_patient[phone].append({'id': c.id, 'vec': vec})

    X_normal_list = []
    
    # Calculate patient means and center their calls
    for phone, p_calls in calls_by_patient.items():
        if len(p_calls) < 2:
            continue # Need at least 2 calls to have a meaningful mean/variance
            
        vectors = np.array([c['vec'] for c in p_calls])
        
        # Special logic for target patient: Exclude the last 2 calls from mean calculation
        if phone == patient.phone and len(vectors) > 2:
            patient_mean = np.mean(vectors[:-2], axis=0)
        else:
            patient_mean = np.mean(vectors, axis=0)
            
        # Center all calls for this patient
        centered_vectors = vectors - patient_mean
        X_normal_list.extend(centered_vectors)

    # --- Step B: PCA Whitening ---
    if len(X_normal_list) < 5:
        # Not enough data globally to fit 5D PCA
        return jsonify({'error': 'Insufficient global data to build PCA cloud (need > 5 calls).'})
        
    X_normal = np.array(X_normal_list)
    pca = PCA(whiten=True)
    pca.fit(X_normal)
    Z_normal = pca.transform(X_normal)

    # --- Step B.2: 2D Projection for Visualization ---
    # We fit a second PCA just to project the 5D whitened data down to 2D for the scatter plot
    pca_2d = PCA(n_components=2)
    pca_2d.fit(Z_normal)
    Z_normal_2d = pca_2d.transform(Z_normal)
    
    # We will sample up to 300 points from the global cloud for background context in the UI
    import random
    if len(Z_normal_2d) > 300:
        indices = np.random.choice(len(Z_normal_2d), 300, replace=False)
        bg_cloud = Z_normal_2d[indices].tolist()
    else:
        bg_cloud = Z_normal_2d.tolist()

    # --- Step C & D: Check target patient's specific calls ---
    target_data = calls_by_patient.get(patient.phone, [])
    
    # Map valid Call IDs to their Z_new vectors and p-values
    anomaly_scores = {}
    
    if target_data:
        t_vectors = np.array([c['vec'] for c in target_data])
        # Re-calculate the exact same mean we used above (excluding last 2 if > 2)
        if len(t_vectors) > 2:
            t_mean = np.mean(t_vectors[:-2], axis=0)
        else:
            t_mean = np.mean(t_vectors, axis=0)
            
        # Center the target patient's calls
        X_new = t_vectors - t_mean
        
        # Transform (Whiten) the target patient's calls
        Z_new_all = pca.transform(X_new)
        
        # Also project the target patient's calls into 2D for the scatter plot
        Z_new_all_2d = pca_2d.transform(Z_new_all)
        
        n_normal = len(Z_normal)
        
        for i, call_info in enumerate(target_data):
            Z_new = Z_new_all[i]
            z_norm = np.linalg.norm(Z_new)
            
            if z_norm < 1e-9:
                p_value = 1.0
            else:
                u = Z_new / z_norm
                proj_cloud = Z_normal @ u
                proj_Z = Z_new @ u
                
                behind = np.sum(proj_cloud >= proj_Z)
                p_value = (behind + 1) / (n_normal + 1)
            
            z_2d = Z_new_all_2d[i]
            # Calculate angle in 2D space (in degrees)
            angle = float(np.degrees(np.arctan2(z_2d[1], z_2d[0])))
            
            anomaly_scores[call_info['id']] = {
                'p_value': float(p_value),
                'x': float(z_2d[0]),
                'y': float(z_2d[1]),
                'dist': float(z_norm),
                'angle': angle
            }

    # Prepare final UI arrays (slice to `visible`)
    n_total = len(patient_calls)
    n_show = min(visible, n_total)
    start = n_total - n_show
    
    display_calls = patient_calls[start:]
    
    dates = []
    call_ids = []
    treatments = []
    utms = []
    genders = []
    ages = []
    patient_topics = []
    
    # We will pass the p-values and 2D data directly
    call_p_values = []
    is_anomaly = []
    pca_2d_points = []
    
    for c in display_calls:
        dates.append(c.created_at.strftime('%Y-%m-%d %H:%M') if c.created_at else None)
        call_ids.append(c.id)
        treatments.append(c.patient_treatment)
        utms.append(_utm_source(c.patient_utm_params))
        genders.append(c.patient_gender)
        ages.append(_age_at(c.patient_birth_year, c.created_at))
        patient_topics.append(c.topics.get('patient_topics', []) if c.topics else [])
        
        score_data = anomaly_scores.get(c.id)
        if score_data:
            call_p_values.append(score_data['p_value'])
            is_anomaly.append(score_data['p_value'] < p_alpha)
            pca_2d_points.append({
                'x': score_data['x'],
                'y': score_data['y'],
                'dist': score_data['dist'],
                'angle': score_data['angle']
            })
        else:
            call_p_values.append(None)
            is_anomaly.append(False)
            pca_2d_points.append(None)

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
        'visible': visible,
        'net_talk_threshold': net_talk_threshold,
        'p_value_threshold': p_alpha,
        'n_calls': n_total,
        'n_visible': n_show,
        'dates':      dates,
        'call_ids':   call_ids,
        'treatments': treatments,
        'utms':       utms,
        'genders':    genders,
        'ages':       ages,
        'patient_topics': patient_topics,
        'call_p_values': call_p_values,
        'is_anomaly': is_anomaly,
        'pca_2d_points': pca_2d_points,
        'bg_cloud': bg_cloud
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
