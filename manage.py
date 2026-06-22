from flask_script import Manager, Server
from flask_migrate import Migrate, MigrateCommand

# from flask_migrate import upgrade as upgrade_database
from automated_survey_flask import app, db, parsers, prepare_app
from automated_survey_flask.models import Survey, Question, Patient, User  # <--- Add Patient and User here

prepare_app()
migrate = Migrate(app, db, render_as_batch=True)

manager = Manager(app)
manager.add_command('db', MigrateCommand)

@manager.command
def runserver(host='0.0.0.0', port=5000, no_reload=False):
    """Runs the server with SSL forced."""
    app.run(
        host=host,
        port=int(port),
        use_reloader=not no_reload,
        ssl_context=('cert/emolter_fullchain.pem', 'cert/emolter.key')
    )

@manager.command
def test():
    """Run the unit tests."""
    import sys
    import unittest

    prepare_app(environment='testing')
    tests = unittest.TestLoader().discover('.', pattern="*_tests.py")
    test_result = unittest.TextTestRunner(verbosity=2).run(tests)

    if not test_result.wasSuccessful():
        sys.exit(1)


@manager.command
def dbseed():
    with open('survey.json') as survey_file:
        db.session.add(parsers.survey_from_json(survey_file.read()))
        db.session.commit()

import json

@manager.command
def seed_patients():
    """Seeds the patients table from patients.json"""
    with open('patients.json', 'r', encoding='utf-8') as f:
        patients_data = json.load(f)
        
    for p in patients_data:
        # Avoid duplicates by checking phone number
        exists = Patient.query.filter_by(phone=p['phone']).first()
        if not exists:
            new_patient = Patient(
                name=p['name'],
                phone=p['phone'],
                language=p.get('language', 'he-IL')
            )
            db.session.add(new_patient)
    
    db.session.commit()
    print("✅ Patients seeded successfully!")


@manager.command
def create_superuser():
    """Create initial superuser account"""
    phone = input("Enter phone number (e.g., +972501234567): ")
    nickname = input("Enter nickname: ")
    password = input("Enter password: ")

    if User.query.filter_by(phone=phone).first():
        print("User with this phone already exists!")
        return

    superuser = User(
        phone=phone,
        nickname=nickname,
        is_superuser=True
    )
    superuser.set_password(password)
    db.session.add(superuser)
    db.session.commit()
    print(f"✅ Superuser created: {superuser.nickname} ({superuser.phone})")


@manager.command
def seed_prosody_params():
    """Seed prosody parameter explanations"""
    from automated_survey_flask.models import ProsodyParameter

    parameters = [
        # Basic Voice Metrics
        ('mean_pitch_hz', 'Mean Pitch', 'Average fundamental frequency of voice in Hertz. Normal ranges: Male (85-180 Hz), Female (165-255 Hz). Lower values may indicate depression or fatigue.', 'basic'),
        ('pitch_sd_hz', 'Pitch SD (Hz)', 'Top-level standard deviation of pitch across the utterance in Hertz. High SD = expressive/emotional speech; very low SD = monotone (possible depression marker).', 'basic'),
        ('pitch_range_hz', 'Pitch Range (Hz)', 'Distance between lowest and highest pitch in Hertz. Compressed range may indicate flat affect; very wide range can reflect agitation or strong emotion.', 'basic'),
        ('mean_intensity_db', 'Mean Intensity', 'Average loudness of voice in decibels. Indicates vocal energy and projection. Low intensity may suggest low energy or depression.', 'basic'),
        ('mean_hnr_db', 'Mean HNR', 'Harmonics-to-Noise Ratio in decibels. Measures voice quality and clarity. Values >15 dB indicate good voice quality, <10 dB may indicate hoarseness or voice disorders.', 'basic'),
        ('f1_mean_hz', 'Formant F1', 'First formant frequency in Hertz. Related to tongue height and jaw opening. Affects vowel quality and speech clarity.', 'basic'),
        ('f2_mean_hz', 'Formant F2', 'Second formant frequency in Hertz. Related to tongue position (front/back). Important for vowel distinction and speech intelligibility.', 'basic'),
        ('voice_health_score', 'Voice Health Score', 'Weighted composite score from 0.0 (clean) to 1.0 (very hoarse). Combines jitter, shimmer, HNR and degree_of_voice_breaks into a single severity indicator.', 'basic'),
        ('speaking_ratio', 'Speaking Ratio', 'Fraction (0-1) of the recording containing active speech vs silence. Very low values indicate long pauses, reluctance, or interviewer-dominated audio.', 'basic'),
        ('total_duration', 'Total Duration (s)', 'Length of the analyzed recording in seconds. Longer samples yield more reliable statistics; very short samples should be interpreted with caution.', 'basic'),

        # Voice Quality Statistics
        ('from_0_to_0_seconds_duration', 'Duration', 'Total duration of the voice sample in seconds. Longer samples provide more reliable analysis.', 'quality'),
        ('number_of_pulses', 'Number of Pulses', 'Total number of glottal pulses detected. Indicates the regularity of vocal fold vibration.', 'quality'),
        ('median_pitch', 'Median Pitch', 'Middle value of pitch distribution in Hertz. More robust than mean pitch against outliers.', 'quality'),
        ('mean_pitch', 'Mean Pitch (VQS)', 'Praat-reported mean fundamental frequency in voice_quality_stats, in Hertz. Closely tracks mean_pitch_hz but is computed from the voice report rather than the pitch object.', 'quality'),
        ('standard_deviation', 'Pitch Std Dev', 'Standard deviation of pitch in Hertz. Measures pitch variability. High values may indicate emotional expressiveness or instability.', 'quality'),
        ('minimum_pitch', 'Min Pitch', 'Lowest pitch detected in Hertz. Part of the pitch range measurement.', 'quality'),
        ('maximum_pitch', 'Max Pitch', 'Highest pitch detected in Hertz. Part of the pitch range measurement.', 'quality'),
        ('number_of_voice_breaks', 'Voice Breaks', 'Number of times voicing was interrupted. High values may indicate voice disorders or emotional distress.', 'quality'),
        ('degree_of_voice_breaks', 'Degree of Voice Breaks', 'Percentage of time with voice breaks. Values >10% may indicate significant voice quality issues.', 'quality'),

        # Jitter (Voice Stability)
        ('jitter_local', 'Jitter Local', 'Cycle-to-cycle variation in pitch period (%). Measures short-term pitch instability. Normal <1%, values >2% may indicate voice pathology or stress.', 'jitter'),
        ('jitter_local,_absolute', 'Jitter Local (Absolute)', 'Absolute cycle-to-cycle variation of the pitch period in microseconds. Unlike jitter_local (a percentage), this is an absolute time-domain measure of period instability.', 'jitter'),
        ('jitter_rap', 'Jitter RAP', 'Relative Average Perturbation - three-point smoothed jitter (%). Measures pitch perturbation. Values >0.68% may be abnormal.', 'jitter'),
        ('jitter_ppq5', 'Jitter PPQ5', 'Five-point Period Perturbation Quotient (%). Smoothed jitter measure. Values >0.84% may indicate voice issues.', 'jitter'),
        ('jitter_ddp', 'Jitter DDP', 'Difference of Differences of Periods (%). Alternative jitter calculation. Three times the RAP value.', 'jitter'),

        # Shimmer (Amplitude Variation)
        ('shimmer_local', 'Shimmer Local', 'Cycle-to-cycle amplitude variation (%). Measures voice stability. Normal <3%, values >5% may indicate hoarseness or voice disorders.', 'shimmer'),
        ('shimmer_local,_db', 'Shimmer Local (dB)', 'Shimmer expressed in decibels. More perceptually relevant than percentage. Values >0.35 dB may be abnormal.', 'shimmer'),
        ('shimmer_apq3', 'Shimmer APQ3', 'Three-point Amplitude Perturbation Quotient (%). Smoothed shimmer measure.', 'shimmer'),
        ('shimmer_apq5', 'Shimmer APQ5', 'Five-point Amplitude Perturbation Quotient (%). More smoothed shimmer measure. Values >3.07% may indicate issues.', 'shimmer'),
        ('shimmer_apq11', 'Shimmer APQ11', 'Eleven-point Amplitude Perturbation Quotient (%). Highly smoothed shimmer. Values >5.2% may be abnormal.', 'shimmer'),
        ('shimmer_dda', 'Shimmer DDA', 'Difference of Differences of Amplitudes (%). Alternative shimmer calculation. Three times the APQ3 value.', 'shimmer'),

        # Harmonics & Noise
        ('mean_autocorrelation', 'Mean Autocorrelation', 'Average autocorrelation coefficient (0-1). Measures periodicity of voice signal. Higher values indicate more periodic (clearer) voice.', 'harmonics'),
        ('mean_noise-to-harmonics_ratio', 'Noise-to-Harmonics Ratio', 'Ratio of noise to harmonic components. Lower is better. High values indicate breathy or rough voice quality.', 'harmonics'),
        ('mean_harmonics-to-noise_ratio', 'Harmonics-to-Noise Ratio', 'Ratio of harmonic to noise components in dB. Same as Mean HNR. Higher values (>15 dB) indicate clearer voice.', 'harmonics'),

        # Voicing Statistics
        ('fraction_of_locally_unvoiced_frames', 'Locally Unvoiced Frames', 'Percentage of frames without clear voicing. High values may indicate breathy voice or voice breaks.', 'voicing'),
        ('number_of_periods', 'Number of Periods', 'Total number of pitch periods detected. More periods allow more reliable analysis.', 'voicing'),
        ('mean_period', 'Mean Period', 'Average pitch period duration in milliseconds. Inversely related to pitch (longer period = lower pitch).', 'voicing'),
        ('standard_deviation_of_period', 'Period Std Dev', 'Variability in pitch period duration (ms). Related to jitter measurements.', 'voicing'),

        # Sentiment Analysis (AESDD / Wav2Vec 2.0 XLSR model)
        ('sentiment_happiness', 'Happiness', 'Probability that the speaker sounds happy. High values indicate positive emotional tone, engagement, or relief.', 'sentiment'),
        ('sentiment_anger', 'Anger', 'Probability that the speaker sounds angry. Elevated values may reflect frustration, agitation, or hostility.', 'sentiment'),
        ('sentiment_sadness', 'Sadness', 'Probability that the speaker sounds sad. High values may indicate low mood, grief, or depressive affect.', 'sentiment'),
        ('sentiment_disgust', 'Disgust', 'Probability that the speaker expresses disgust. May reflect strong aversion or contempt in vocal tone.', 'sentiment'),
        ('sentiment_fear', 'Fear', 'Probability that the speaker sounds fearful or anxious. High values may indicate distress or heightened arousal.', 'sentiment'),
    ]

    for param_key, param_name, explanation, category in parameters:
        existing = ProsodyParameter.query.filter_by(parameter_key=param_key).first()
        if not existing:
            param = ProsodyParameter(
                parameter_key=param_key,
                parameter_name=param_name,
                explanation=explanation,
                category=category
            )
            db.session.add(param)
            print(f"✅ Added: {param_name}")
        else:
            print(f"⏭️  Skipped (exists): {param_name}")

    db.session.commit()
    print(f"\n🎉 Prosody parameters seeded successfully!")


@manager.command
def seed_question_sets():
    """Import all questions_*.json files into the question_sets DB table."""
    import glob
    from automated_survey_flask.models import QuestionSet

    files = glob.glob("questions_*.json")
    if not files:
        print("No questions_*.json files found.")
        return

    imported = skipped = 0
    for filepath in sorted(files):
        filename = filepath.replace("questions_", "").replace(".json", "")
        parts = filename.split("_", 1)
        if len(parts) != 2:
            print(f"Skipping {filepath} — unexpected filename format")
            continue
        batch, lang = parts[0], parts[1]

        if QuestionSet.query.filter_by(batch=batch, lang=lang).first():
            print(f"⏭️  Skipped (exists): {batch} / {lang}")
            skipped += 1
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            content = json.load(f)

        qs = QuestionSet(batch=batch, lang=lang, content=content)
        db.session.add(qs)
        print(f"✅ Imported: {batch} / {lang}  ({len(content.get('questions', []))} questions)")
        imported += 1

    db.session.commit()
    print(f"\nDone — {imported} imported, {skipped} skipped.")


@manager.command
def run_scheduled_calls():
    """Fire any patient call schedules that are due. Intended to be run hourly via cron:
        0 * * * * cd /home/mtrapp/appl/automated-survey-flask && venv_38/bin/python manage.py run_scheduled_calls
    """
    from datetime import datetime
    from twilio.rest import Client
    from urllib.parse import quote
    import os as _os
    from automated_survey_flask.models import Call, CallSchedule, Patient
    from automated_survey_flask.therapist_view import snapshot_patient_to_call

    TWILIO_NUMBER = "+17473023043"
    BASE_URL      = "https://www.emolter.org:5000"

    client = Client(_os.environ.get('TWILIO_ACCOUNT_SID'), _os.environ.get('TWILIO_AUTH_TOKEN'))

    now = datetime.now()
    schedules = CallSchedule.query.filter_by(active=True, hour=now.hour).all()
    print(f"[scheduler] {now.isoformat(timespec='minutes')} — {len(schedules)} active schedule(s) at hour {now.hour}")

    fired = 0
    for sched in schedules:
        if not sched.is_due(now):
            continue
        patient = Patient.query.filter_by(id=sched.patient_id, deleted=False).first()
        if not patient:
            print(f"[scheduler] schedule {sched.id} skipped — patient {sched.patient_id} missing or deleted")
            continue

        try:
            tw_call = client.calls.create(
                to=patient.phone,
                from_=TWILIO_NUMBER,
                url=(
                    f'{BASE_URL}/llm-relay-voice?lang={quote(patient.language)}'
                    f'&name={quote(patient.nickname or patient.name)}'
                    f'&batch={quote(patient.batch or "basic")}'
                    f'&to_phone={quote(patient.phone)}'
                    f'&from_phone={quote(TWILIO_NUMBER)}'
                ),
                record=True,
                recording_channels='dual',
                recording_status_callback=f'{BASE_URL}/llm-recording-callback',
                recording_status_callback_method='POST',
            )

            call_record = Call(
                callSid=tw_call.sid,
                recordSid=None,
                questionId=0,
                recordingUrl=None,
                conversationText='[]',
                patientPhone=patient.phone,
                carrierPhone=TWILIO_NUMBER,
                questionsFile=f"questions_{patient.batch or 'basic'}_{patient.language}.json",
            )
            snapshot_patient_to_call(call_record, patient)
            db.session.add(call_record)

            sched.last_run_at = now
            db.session.commit()
            print(f"[scheduler] fired LLM call for patient {patient.id} ({patient.phone}) — SID {tw_call.sid}")
            fired += 1
        except Exception as e:
            db.session.rollback()
            print(f"[scheduler] FAILED for patient {patient.id}: {e}")

    print(f"[scheduler] done — fired {fired} call(s)")


@manager.command
def delete_today_calls():
    """Delete all Call rows created today."""
    from automated_survey_flask.models import Call
    from datetime import date
    today = date.today()
    rows = Call.query.filter(db.func.date(Call.created_at) == today).all()
    count = len(rows)
    if count == 0:
        print("No calls found for today.")
        return
    for row in rows:
        db.session.delete(row)
    db.session.commit()
    print(f"Deleted {count} call row(s) created today ({today}).")


if __name__ == "__main__":
    manager.run()
