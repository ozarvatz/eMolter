import json
import random
import numpy as np
from automated_survey_flask import app, db
from automated_survey_flask.models import Call
from automated_survey_flask.patient_report_view import _extract_net_talk

def _safe_float(v):
    if v is None: return None
    try:
        f = float(v)
        if f != f or f == float('inf') or f == float('-inf'): return None
        return f
    except (TypeError, ValueError): return None

def _extract_5d_vector(pr):
    if not isinstance(pr, dict): return None
    # x1: pause, x2: spiking, x3: f0, x4: jitter, x5: cpp
    pause = _safe_float(pr.get('pause_duration'))
    spiking = _safe_float(pr.get('spiking_rate'))
    f0 = _safe_float(pr.get('f0_variability_hz'))
    vqs = pr.get('voice_quality_stats', {})
    jitter = _safe_float(vqs.get('jitter_local'))
    cpp = _safe_float(pr.get('cpp'))
    if None in (pause, spiking, f0, jitter, cpp): return None
    return [pause, spiking, f0, jitter, cpp]

def generate():
    with app.app_context():
        # Force database loading
        all_calls = Call.query.filter(Call.is_processed == True, Call.prosody_results.isnot(None)).all()
        calls_by_patient = {}
        for c in all_calls:
            nt = _extract_net_talk(c.prosody_results)
            if nt is not None and nt >= 30:
                vec = _extract_5d_vector(c.prosody_results)
                if vec is not None:
                    calls_by_patient.setdefault(c.patient_phone, []).append(vec)
        
        x_ranges = {0: [], 1: [], 2: [], 3: [], 4: []}
        for phone, vecs in calls_by_patient.items():
            if len(vecs) < 2: continue
            arr = np.array(vecs)
            for i in range(5):
                c_min, c_max = np.min(arr[:, i]), np.max(arr[:, i])
                dist = abs(c_max - c_min)
                # Expand range by 40% total (20% down, 20% up)
                r_min = c_min - 0.2 * dist
                r_max = c_max + 0.2 * dist
                x_ranges[i].append((float(r_min), float(r_max)))
                
        if not x_ranges[0]:
            # Fallback if no real data: use some dummy standard ranges
            x_ranges = {
                0: [(0.1, 0.5)], 1: [(1.0, 4.0)], 2: [(10.0, 50.0)],
                3: [(0.01, 0.05)], 4: [(10.0, 20.0)]
            }
            
        virt_db = {}
        for v_id in range(1, 101):
            phone = f"v{v_id:03d}"
            chosen_ranges = [random.choice(x_ranges[i]) for i in range(5)]
            
            vp_calls = []
            for c_id in range(1, 101):
                vec = [random.uniform(r[0], r[1]) for r in chosen_ranges]
                vp_calls.append({
                    'id': f"v_{phone}_{c_id}",
                    'vec': vec,
                    'date': f"2026-07-01 {c_id//4:02d}:{(c_id%4)*15:02d}"
                })
            virt_db[phone] = vp_calls
            
        json_path = os.path.join(app.root_path, 'virtual_patients.json')
        with open(json_path, 'w') as f:
            json.dump(virt_db, f)
        return True

import os
if __name__ == "__main__":
    generate()
