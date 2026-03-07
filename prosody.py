import sys
import json
import requests
import parselmouth
import tempfile
import os
import re
from parselmouth.praat import call
from automated_survey_flask import app
from automated_survey_flask.models import db, Call

def parse_voice_report(report_str):
    """Converts the raw Praat text report into a dictionary."""
    data = {}
    lines = report_str.split('\n')
    for line in lines:
        # Match lines like "Jitter (local): 0.015%" or "Mean pitch: 120.5 Hz"
        match = re.search(r'([^:]+):\s+([\d\.]+)', line)
        if match:
            key = match.group(1).strip().lower().replace(" ", "_")
            key = re.sub(r'[()]', '', key)
            try:
                data[key] = float(match.group(2))
            except ValueError:
                data[key] = match.group(2)
    return data

def get_prosody_features(url, channel_index=1):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        tmp_path = tmp_file.name
        try:
            # 1. Stream download
            response = requests.get(url, stream=True)
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=8192):
                tmp_file.write(chunk)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

            # 2. Load into Parselmouth
            full_sound = parselmouth.Sound(tmp_path)
            
            # 3. Safe Channel Extraction
            num_channels = call(full_sound, "Get number of channels")
            if num_channels > 1:
                # Only extract if it's actually Stereo
                sound = call(full_sound, "Extract channel", channel_index)
            else:
                # If it's already Mono, just use the sound as-is
                sound = full_sound

            features = {}

            # --- Pitch & Intensity ---
            pitch = sound.to_pitch()
            features["mean_pitch_hz"] = call(pitch, "Get mean", 0, 0, "Hertz")
            
            intensity = sound.to_intensity()
            features["mean_intensity_db"] = call(intensity, "Get mean", 0, 0, "energy")

            # --- Glottal Pulses & Voice Quality ---
            # PointProcess is used for individual glottal pulse timing
            pulses = call([sound, pitch], "To PointProcess (cc)")
            
            # This generates the "everything" report (Jitter, Shimmer, HNR, etc.)
            raw_report = call([sound, pitch, pulses], "Voice report", 0, 0, 75, 500, 1.3, 1.6, 0.03, 0.45)
            features["voice_quality_stats"] = parse_voice_report(raw_report)

            # Voice Report (Comprehensive Glottal Analysis)
            # This returns a long string of data about pulses, breaks, and glottal cycles
            # voice_report = call([sound, pitch, pulses], "Voice report", 0, 0, 75, 500, 1.3, 1.6, 0.03, 0.45)
            # features["glottal_voice_report"] = voice_report
                    
            # --- Formants (Vocal Tract resonance) ---
            formant = sound.to_formant_burg()
            features["f1_mean_hz"] = call(formant, "Get mean", 1, 0, 0, "Hertz")
            features["f2_mean_hz"] = call(formant, "Get mean", 2, 0, 0, "Hertz")

            # --- Harmonicity ---
            harmonicity = call(sound, "To Harmonicity (cc)", 0.01, 75, 0.1, 1.0)
            features["mean_hnr_db"] = call(harmonicity, "Get mean", 0, 0)

            return features

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

if __name__ == "__main__":
    call_snippet = None
    url_arg = None
    ch = 1
    with app.app_context():
        db.init_app(app)
        
        if len(sys.argv) < 2:
            print("Usage: python prosody.py <URL> [channel_index]")
            call_snippet = Call.query.filter_by(is_processed=False).order_by(Call.created_at).first()
            url_arg = call_snippet.recording_url
            ch = 1
            # sys.exit(1)
        else:
            url_arg = sys.argv[1] if len(sys.argv) >= 1 else None
            ch = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        
        try:
            results = get_prosody_features(url_arg, ch)
            jsonProsody = json.dumps(results, indent=4)
            print(jsonProsody)
            if call_snippet:
                call_snippet.prosody_results = jsonProsody
                call_snippet.is_processed = True
                db.session.commit()
        except Exception as e:
            print(json.dumps({"error": str(e)}, indent=4))