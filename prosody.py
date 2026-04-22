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
import torch
import librosa
from transformers import pipeline

classifier = pipeline("audio-classification", model="harshit345/xlsr-wav2vec-speech-emotion-recognition")

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

def get_voice_health_score(voice_stats):
    """
    Adjusted for Twilio/Telephony Audio.
    Returns 0.0 (Clean) to 1.0 (Very Hoarse).
    """
    # 1. Jitter: Phone lines add jitter. 
    jitter = voice_stats.get('jitter_local', 0)
    jitter_norm = jitter / 0.025 # 2.5% is now the 'very high' mark

    # 2. Shimmer: Twilio compression affects amplitude.
    shimmer = voice_stats.get('shimmer_local', 0)
    shimmer_norm = shimmer / 0.10 # 10% is now the 'very high' mark

    # 3. HNR: This is the most reliable for phone calls.
    # Normal is >15dB. If it drops below 10dB, it's hoarse.
    hnr = voice_stats.get('mean_harmonics-to-noise_ratio', 20)
    # We convert HNR to a 0-1 scale where 20dB=0 and 5dB=1
    hnr_norm = (20 - hnr) / 15 
    hnr_norm = max(0, min(hnr_norm, 1.0))

    # 4. Voice Breaks: Very common in Twilio recordings.
    # We significantly reduce the impact of this parameter.
    breaks = voice_stats.get('degree_of_voice_breaks', 0)
    breaks_norm = (breaks / 50.0) # Only starts becoming a major issue above 50%

    # NEW WEIGHTED CALCULATION
    # We give HNR the most weight because it's the most stable on phone calls.
    score = (jitter_norm * 0.2) + (shimmer_norm * 0.2) + (hnr_norm * 0.5) + (breaks_norm * 0.1)
    
    return round(max(0.0, min(score, 1.0)), 3)

def analyze_emotions(audio_path, sr=16000, max_seconds=30):
    """
    Analyzes the emotional content of an audio recording using
    the Wav2Vec 2.0 XLSR model.

    Processes the raw audio signal to extract probability scores for
    Happiness, Anger, Sadness, Disgust, and Fear based on acoustic
    features and speech patterns.

    Args:
        audio_path (str): Path to the Twilio .wav recording.
        sr (int): Target sampling rate (standard 16kHz for AI models).
        max_seconds (int): Maximum audio duration to load (avoids OOM on long recordings).

    Returns:
        dict: A mapping of emotion labels to their confidence scores.

    *** The model was trained on a dataset called AESDD (Acted Emotional Speech Dynamic Database)
    """
    # 1. Load and Resample (duration cap prevents OOM on long full-call recordings)
    speech, sr = librosa.load(audio_path, sr=16000, duration=max_seconds)

    # 2. Pass the 'speech' variable (the numbers) to the AI, not the file path
    results = classifier(speech) 

    normal_result = {}
    for element in results:
        normal_result[element["label"]] = element["score"] 
    # 3. Output the result
    print(normal_result)

    return normal_result


"""
the function return's json with the follow parameters: 
Core Prosody (The "Melody")

    mean_pitch_hz: The average "highness" or "lowness" of the voice.

    pitch_sd_hz: How much the pitch varies. High SD = expressive/emotional; Low SD = monotone (potential depression marker).

    pitch_range_hz: The distance between the lowest and highest note hit.

    mean_intensity_db: The average volume. (Yours is ~70 due to the scaling we added).

    speaking_ratio: The percentage of the recording containing actual speech vs. silence. 0.55 means they were talking about 55% of the time.

Voice Quality (Physical Health & Stability)

These measure the "micro-wobbles" in the vocal folds.

    jitter_local (0.697%): Frequency instability. Values below 1.04% are considered healthy/normal.

    shimmer_local (5.837%): Amplitude (volume) instability. Values above 3.81% (like yours) can indicate slight breathiness or raspiness.

    mean_noise-to-harmonics (NHR): The amount of "hiss" in the voice. Lower is better.

    mean_harmonics-to-noise (HNR): The "pureness" of the voice. 19.1 dB is a very good, clean signal for a phone call.

Voice Breaks (Fluency)

    number_of_voice_breaks: How many times the voice cut out unexpectedly (not a pause, but a "crack").

    degree_of_voice_breaks: The percentage of the speech that was "broken." 30% is quite high—this might be why your voice_health_score hit 1.0.

Resonance (Vocal Tract)

    f1_mean_hz / f2_mean_hz: These are Formants. They don't represent pitch, but the shape of the mouth. They are used to identify vowels and "vocal brightness."

"""
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

            # 2. Load into Parselmouth and scale
            full_sound = parselmouth.Sound(tmp_path)
            full_sound.scale_intensity(70)
            # 3. Safe Channel Extraction
            num_channels = call(full_sound, "Get number of channels")
            if num_channels > 1:
                # Only extract if it's actually Stereo
                sound = call(full_sound, "Extract one channel", channel_index)
            else:
                # If it's already Mono, just use the sound as-is
                sound = full_sound

            features = {}

            # --- Pitch & Intensity ---
            pitch = sound.to_pitch()
            features["mean_pitch_hz"] = call(pitch, "Get mean", 0, 0, "Hertz")
            features["pitch_sd_hz"] = call(pitch, "Get standard deviation", 0, 0, "Hertz")
            features["pitch_range_hz"] = call(pitch, "Get maximum", 0, 0, "Hertz", "Parabolic") - call(pitch, "Get minimum", 0, 0, "Hertz", "Parabolic")

            intensity = sound.to_intensity()
            features["mean_intensity_db"] = call(intensity, "Get mean", 0, 0, "energy")
            
            # --- Glottal Pulses & Voice Quality ---
            # PointProcess is used for individual glottal pulse timing
            pulses = call([sound, pitch], "To PointProcess (cc)")
            
            # This generates the "everything" report (Jitter, Shimmer, HNR, etc.)
            raw_report = call([sound, pitch, pulses], "Voice report", 0, 0, 75, 500, 1.3, 1.6, 0.03, 0.45)
            features["voice_quality_stats"] = parse_voice_report(raw_report)
            features["voice_health_score"] = get_voice_health_score(features["voice_quality_stats"])        
            # --- Formants (Vocal Tract resonance) ---
            formant = sound.to_formant_burg()
            features["f1_mean_hz"] = call(formant, "Get mean", 1, 0, 0, "Hertz")
            features["f2_mean_hz"] = call(formant, "Get mean", 2, 0, 0, "Hertz")

            # --- Harmonicity ---
            harmonicity = call(sound, "To Harmonicity (cc)", 0.01, 75, 0.1, 1.0)
            features["mean_hnr_db"] = call(harmonicity, "Get mean", 0, 0)

            ## --- Speech rate & Puse ---
            intensities = intensity.values[0]
            threshold = features["mean_intensity_db"] - 10 # 10dB below mean is often silence
            voiced_frames = [i for i in intensities if i > threshold]
            if len(intensities) > 0:
                features["speaking_ratio"] = len(voiced_frames) / len(intensities)
            else:
                features["speaking_ratio"] = 0.0

            features["total_duration"] = sound.get_total_duration()
            features["sentiment"] = analyze_emotions(tmp_path, sr=16000)

            return features

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

if __name__ == "__main__":
    call_snippet = None
    url_arg = None
    ch = 1
    if len(sys.argv) >= 2:
        print("Usage: python prosody.py <URL> [channel_index]")
        url_arg = sys.argv[1] if len(sys.argv) >= 1 else None
        ch = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        try:
            results = get_prosody_features(url_arg, ch)
            jsonProsody = json.dumps(results, indent=4)
            print(jsonProsody)
            sys.exit(1)
        except Exception as e:
            print(json.dumps({"error": str(e)}, indent=4))
    
    with app.app_context():
        db.init_app(app)
        while True:
            call_snippet = Call.query.filter_by(is_processed=False).order_by(Call.created_at).first()
            if not call_snippet:
                print("All rows in the DB Calls table have already been processed.")
                sys.exit(1)
            
            if not call_snippet.recording_url:
                print(f"There is no URL for record, call sid: {call_snippet.call_sid}")
                call_snippet.is_processed = True
                db.session.commit()
                continue

            url_arg = call_snippet.recording_url
            ch = 1
        
            try:
                results = get_prosody_features(url_arg, ch)
                print(f"prosody for call id({call_snippet.id}): {json.dumps(results, indent=4)}")
                # jsonProsody = json.dumps(results, indent=4)
                call_snippet.prosody_results = results
                call_snippet.is_processed = True
                db.session.commit()
                
            except Exception as e:
                print(json.dumps({"error": str(e),
                                  "call_sid": call_snippet.call_sid,
                                  "record_sid": call_snippet.record_sid}, indent=4))
            