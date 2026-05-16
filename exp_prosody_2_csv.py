import pandas as pd
import json
from automated_survey_flask import app
from automated_survey_flask.models import db, Call
import os

EXPORT_DIR = os.path.join(app.static_folder, 'exports')

with app.app_context():
    db.init_app(app)
    # 1. Query the data from the database   
    query = db.session.query(Call).statement
    # query = db.session.query(Call).filter(Call.created_at >= start_date).statement
    df = pd.read_sql(query, db.engine)

    # 2. Flatten the JSON column
    # This turns {'mean_pitch_hz': 138, ...} into separate columns
    json_struct = df['prosody_results'].apply(lambda x: x if isinstance(x, dict) else json.loads(x or '{}'))
    df_json = pd.json_normalize(json_struct)

    # 3. Combine with original data (dropping the raw JSON column)
    df_final = pd.concat([df.drop('prosody_results', axis=1), df_json], axis=1)

    # 3b. Serialize patient_utm_params JSON back to a single "k1=v1,k2=v2" string column
    if 'patient_utm_params' in df_final.columns:
        def _utm_to_str(x):
            d = x if isinstance(x, dict) else (json.loads(x) if x else {})
            return ','.join(f'{k}={v}' for k, v in d.items())
        df_final['patient_utm_params'] = df_final['patient_utm_params'].apply(_utm_to_str)

    # 4. Export to CSV
    filename = "prosody_report.csv"
    filepath = os.path.join(EXPORT_DIR, filename)
    print(f"file path : {filepath}")
    df_final.to_csv(filepath, index=False)
    print("Export complete: prosody_export.csv")
