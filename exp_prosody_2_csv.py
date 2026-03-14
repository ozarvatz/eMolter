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

    # 4. Export to CSV
    filename = "prosody_report.csv"
    filepath = os.path.join(EXPORT_DIR, filename)
    print(f"file path : {filepath}")
    df_final.to_csv(filepath, index=False)
    print("Export complete: prosody_export.csv")
