from flask import render_template, jsonify, request, redirect, url_for, flash
from flask_login import login_required
import os
import json
from automated_survey_flask import app, db

@app.route('/therapist/algo-test')
@login_required
def algo_test_page():
    json_path = os.path.join(app.root_path, 'virtual_patients.json')
    virt_patients = []
    
    if os.path.exists(json_path):
        with open(json_path, 'r') as f:
            virt_db = json.load(f)
            
        import numpy as np
        from sklearn.decomposition import PCA
        
        # 1. Build Global Cloud
        X_normal_list = []
        patient_means = {}
        for p, calls in virt_db.items():
            vecs = np.array([c['vec'] for c in calls])
            mean = np.mean(vecs[:-2], axis=0) if len(vecs) > 2 else np.mean(vecs, axis=0)
            patient_means[p] = mean
            X_normal_list.extend(vecs - mean)
            
        X_normal = np.array(X_normal_list)
        pca = PCA(whiten=True).fit(X_normal)
        Z_normal = pca.transform(X_normal)
        n_normal = len(Z_normal)
        
        # 2. Score last 2 calls for each virtual patient
        for p, calls in virt_db.items():
            vecs = np.array([c['vec'] for c in calls])
            last_2 = vecs[-2:] - patient_means[p]
            Z_last_2 = pca.transform(last_2)
            
            min_p = 1.0
            for Z_new in Z_last_2:
                z_norm = np.linalg.norm(Z_new)
                if z_norm < 1e-9: continue
                u = Z_new / z_norm
                behind = np.sum(Z_normal @ u >= Z_new @ u)
                p_val = (behind + 1) / (n_normal + 1)
                min_p = min(min_p, p_val)
                
            virt_patients.append({
                'phone': p, 
                'min_p': float(min_p)
            })
            
        # 3. Sort by most anomalous (lowest p-value)
        virt_patients.sort(key=lambda x: x['min_p'])
    
    return render_template('algo_test.html', virt_patients=virt_patients)

@app.route('/api/therapist/algo-test/regenerate', methods=['POST'])
@login_required
def algo_test_regenerate():
    from . import generate_virtual_patients
    success = generate_virtual_patients.generate()
    if success:
        return jsonify({'status': 'success'})
    else:
        return jsonify({'status': 'error', 'message': 'Regeneration failed.'}), 500
