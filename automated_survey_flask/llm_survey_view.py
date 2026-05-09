import os
import json
import time
import threading
import random
from urllib.parse import quote
from datetime import datetime

from flask import request, session, Response, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather

from . import app
from .models import db, Call, Patient

# In-memory store for pre-computed LLM questions: call_sid -> {"q": str|None, "event": Event}
_pending_questions = {}

FILLERS = {
    'he-IL': ["מעניין... תן לי לחשוב רגע.", "אני שומע אותך... רגע אחד.", "בסדר... נמשיך."],
    'de-DE': ["Interessant... einen Moment bitte.", "Ich verstehe... lass mich kurz überlegen.", "Gut... einen Augenblick."],
    'en-US': ["Interesting... let me think for a moment.", "I see... just a second.", "I understand... one moment."],
}

def _get_filler(lang):
    options = FILLERS.get(lang) or FILLERS['en-US']
    return random.choice(options)

# ---------------------------------------------------------------------------
# Constants (mirror survey_view.py conventions)
# ---------------------------------------------------------------------------
def _twilio_lang(lang):
    """Map language codes to Twilio-compatible codes. Google TTS uses iw-IL for Hebrew."""
    return 'iw-IL' if lang == 'he-IL' else lang


SORRY_FAILED  = 1
THANKS        = 3
HELLO         = 4
VOICE_ALGO    = 1

LLM_MODEL         = os.environ.get('GROQ_MODEL', 'llama-3.1-8b-instant')
MAX_HISTORY_CHARS = 3000
TWILIO_NUMBER     = "+17473023043"
BASE_URL          = "https://www.emolter.org:5000"

# ---------------------------------------------------------------------------
# Groq client (lazy-initialised so a missing key only fails when routes hit)
# ---------------------------------------------------------------------------
_groq_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        api_key = os.environ.get('GROQ_API_KEY')
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


# ---------------------------------------------------------------------------
# Audio transcription via Groq Whisper
# ---------------------------------------------------------------------------
def _transcribe_audio(recording_url, lang):
    """Download a Twilio recording and transcribe it with Groq Whisper."""
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token  = os.environ.get('TWILIO_AUTH_TOKEN')
    # Convert BCP-47 ('he-IL') to Whisper ISO-639-1 ('he')
    whisper_lang = lang.split('-')[0] if lang and '-' in lang else lang

    t0 = time.time()
    resp = http_requests.get(recording_url, auth=(account_sid, auth_token), timeout=15)
    resp.raise_for_status()
    print(f"[TIMING] Recording download: {(time.time()-t0)*1000:.1f}ms ({len(resp.content)} bytes)")

    t0 = time.time()
    transcription = _get_groq_client().audio.transcriptions.create(
        file=('recording.wav', resp.content),
        model='whisper-large-v3-turbo',
        language=whisper_lang,
        response_format='text',
    )
    result = transcription.strip() if isinstance(transcription, str) else str(transcription).strip()
    print(f"[TIMING] Groq Whisper: {(time.time()-t0)*1000:.1f}ms → '{result}'")
    return result


# ---------------------------------------------------------------------------
# JSON helpers (self-contained — survey_view.py not imported to avoid coupling)
# ---------------------------------------------------------------------------
def _read_from_json(lang, batch, n, entity):
    from automated_survey_flask.question_service import read_question
    return read_question(lang, batch, n, entity)


def _get_base_questions(lang, batch):
    from automated_survey_flask.question_service import get_questions_list
    return get_questions_list(lang, batch)


# ---------------------------------------------------------------------------
# History helpers  (stored in Call.conversation_text as a JSON array)
# Each entry: {"q": "<question>", "a": "<answer>"}
# An empty "a" means the question was asked but the answer hasn't arrived yet.
# question_id=0 is the sentinel for LLM-survey records.
# ---------------------------------------------------------------------------
def _load_history(call_sid):
    t0 = time.time()
    call = Call.query.filter_by(call_sid=call_sid, question_id=0).first()
    print(f"[TIMING] DB read history: {(time.time()-t0)*1000:.1f}ms")
    if not call or not call.conversation_text:
        return []
    try:
        return json.loads(call.conversation_text)
    except (json.JSONDecodeError, TypeError):
        return []


def _save_history(call_sid, history):
    t0 = time.time()
    call = Call.query.filter_by(call_sid=call_sid, question_id=0).first()
    if call:
        call.conversation_text = json.dumps(history, ensure_ascii=False)
        db.session.commit()
    print(f"[TIMING] DB write history: {(time.time()-t0)*1000:.1f}ms")


def _truncate_history(history):
    """Drop oldest Q&A pairs until the serialised history fits MAX_HISTORY_CHARS."""
    while len(history) > 1:
        if len(json.dumps(history, ensure_ascii=False)) <= MAX_HISTORY_CHARS:
            break
        history = history[1:]
    return history


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------
def _ask_llm(lang, batch, history, q_num, max_questions):
    """Ask Groq to generate the next survey question."""
    base_qs  = _get_base_questions(lang, batch)
    numbered = "\n".join(f"{i+1}. {q}" for i, q in enumerate(base_qs))

    # Build history text (only entries that have a real answer)
    history_lines = ""
    for i, entry in enumerate(history):
        if entry.get('a') and entry['a'] != '[no answer]':
            history_lines += f"Q{i+1}: {entry['q']}\nA{i+1}: {entry['a']}\n"

    system_prompt = (
        f"You are a warm, empathetic mental health interviewer conducting a voice survey by phone.\n\n"
        f"LANGUAGE RULE (absolute — no exceptions):\n"
        f"You MUST write every single word of your output in the language of locale {lang}. "
        f"Never switch to any other language, even if the patient's transcribed answer appears "
        f"to be in a different language. Phone speech-to-text is imperfect; assume the patient "
        f"is always speaking {lang} and infer their intent from context.\n\n"
        f"Topics to explore during the survey (a flexible guide — NOT a fixed script):\n"
        f"{numbered}\n\n"
        f"Rules — follow these strictly:\n"
        f"1. SKIP any topic the patient already answered directly OR indirectly. "
        f"If they said they feel terrible, never ask 'are you feeling well?' — that is already answered. "
        f"Move to a genuinely new angle.\n"
        f"2. If the patient shared something sad, painful, or difficult, briefly acknowledge it with warmth "
        f"before moving forward — never ignore their emotional state.\n"
        f"3. React to what the patient JUST said, not to the fixed topic list. "
        f"Let the conversation flow naturally from their last answer.\n"
        f"4. Be warm, informal, and human — like a caring person, not a questionnaire.\n"
        f"5. Ask exactly ONE short question (one sentence, suitable for a phone call).\n"
        f"6. Output ONLY the question text — no labels, no preamble, no explanations.\n"
        f"7. This is question {q_num} of {max_questions} total."
    )

    user_content = (
        f"Conversation so far:\n{history_lines}\n\n"
        f"The patient's last answer was: \"{history[-1]['a'] if history and history[-1].get('a') else ''}\"\n"
        f"Generate question {q_num}, reacting to what they just said."
        if history_lines else
        f"Generate question {q_num} (opening question)."
    )

    client = _get_groq_client()
    t0 = time.time()
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        max_tokens=80,
        temperature=0.7,
    )
    print(f"[TIMING] Groq API call: {(time.time()-t0)*1000:.1f}ms")
    return resp.choices[0].message.content.strip()


def _build_llm_messages(lang, batch, history, q_num, max_questions):
    """Build the system/user message pair for Groq (shared by blocking and streaming callers)."""
    base_qs  = _get_base_questions(lang, batch)
    numbered = "\n".join(f"{i+1}. {q}" for i, q in enumerate(base_qs))

    history_lines = ""
    for i, entry in enumerate(history):
        if entry.get('a') and entry['a'] != '[no answer]':
            history_lines += f"Q{i+1}: {entry['q']}\nA{i+1}: {entry['a']}\n"

    system_prompt = (
        f"You are a warm, empathetic mental health interviewer conducting a voice survey by phone.\n\n"
        f"LANGUAGE RULE (absolute — no exceptions):\n"
        f"You MUST write every single word of your output in the language of locale {lang}. "
        f"Never switch to any other language, even if the patient's transcribed answer appears "
        f"to be in a different language.\n\n"
        f"Topics to explore (a flexible guide — NOT a fixed script):\n{numbered}\n\n"
        f"Rules:\n"
        f"1. SKIP topics already answered directly or indirectly.\n"
        f"2. Briefly acknowledge pain or sadness before moving on.\n"
        f"3. React to what the patient JUST said.\n"
        f"4. Be warm and informal.\n"
        f"5. Ask exactly ONE short question (one sentence).\n"
        f"6. Output ONLY the question text — no labels, no preamble.\n"
        f"7. This is question {q_num} of {max_questions} total."
    )
    user_content = (
        f"Conversation so far:\n{history_lines}\n\n"
        f"The patient's last answer was: \"{history[-1]['a'] if history and history[-1].get('a') else ''}\"\n"
        f"Generate question {q_num}, reacting to what they just said."
        if history_lines else
        f"Generate question {q_num} (opening question)."
    )
    return system_prompt, user_content


def ask_llm_stream(lang, batch, history, q_num, max_questions):
    """Generator — yields text tokens from Groq for use with ConversationRelay streaming."""
    system_prompt, user_content = _build_llm_messages(lang, batch, history, q_num, max_questions)
    client = _get_groq_client()
    t0 = time.time()
    stream = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        max_tokens=80,
        temperature=0.7,
        stream=True,
    )
    for chunk in stream:
        token = chunk.choices[0].delta.content
        if token:
            yield token
    print(f"[TIMING] Groq stream complete: {(time.time()-t0)*1000:.1f}ms")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/llm-voice', methods=['GET', 'POST'])
def llm_voice_survey():
    lang         = request.args.get('lang') or 'he-IL'
    patient_name = request.args.get('name') or 'noname'
    batch        = request.args.get('batch') or 'basic'
    call_sid     = request.values.get('CallSid', '')
    from_phone   = request.args.get('from_phone', '')
    to_phone     = request.args.get('to_phone', '')

    print(f"+++++ llm_voice_survey {datetime.now()} call_sid={call_sid} +++++")

    twiml      = VoiceResponse()
    voice_model = _read_from_json(lang, batch, VOICE_ALGO, "config")

    try:
        base_qs       = _get_base_questions(lang, batch)
        max_questions = len(base_qs)
        sorry         = _read_from_json(lang, batch, SORRY_FAILED, "messages")

        # Q1 always comes from the JSON — it is the greeting and contains the patient name.
        # The LLM takes over from Q2 onwards.
        intro_q = _read_from_json(lang, batch, 1, "questions").format(patient_name)

        # Upsert the LLM-survey Call record (question_id=0 sentinel)
        call_record = (
            Call.query.filter_by(call_sid=call_sid, question_id=0).first()
            if call_sid else None
        )
        if not call_record and call_sid:
            call_record = Call(
                callSid=call_sid,
                recordSid=None,
                questionId=0,
                recordingUrl=None,
                conversationText='[]',
                patientPhone=to_phone,
                carrierPhone=from_phone,
                questionsFile=f"questions_{batch}_{lang}.json",
            )
            db.session.add(call_record)
            db.session.commit()

        # Store Q1 in history with empty answer placeholder
        if call_record:
            call_record.conversation_text = json.dumps(
                [{"q": intro_q, "a": ""}], ensure_ascii=False
            )
            db.session.commit()

        # 1-second pause absorbs the patient's pickup "hello" before Gather starts.
        twiml.pause(length=1)
        gather = Gather(
            input='speech',
            speech_timeout='auto',
            language=_twilio_lang(lang),
            speech_model='phone_call',
            action=(
                f'/llm-handle-speech?q_num=1&lang={lang}'
                f'&name={quote(patient_name)}&batch={batch}&max_q={max_questions}'
            ),
            method='POST',
        )
        gather.say(intro_q, language=_twilio_lang(lang), voice=voice_model)
        twiml.append(gather)
        twiml.say(sorry, language=_twilio_lang(lang), voice=voice_model)
        twiml.hangup()

    except Exception as e:
        import traceback
        print(f"Error in llm_voice_survey: {e}")
        traceback.print_exc()
        twiml.say("Sorry, there was a technical error.", language=_twilio_lang(lang))
        twiml.hangup()

    return Response(str(twiml), mimetype='text/xml')


@app.route('/llm-handle-speech', methods=['POST'])
def llm_handle_speech():
    lang          = request.args.get('lang') or 'he-IL'
    patient_name  = request.args.get('name') or 'noname'
    batch         = request.args.get('batch') or 'basic'
    q_num         = int(request.args.get('q_num', 1))
    max_questions = int(request.args.get('max_q', 5))
    call_sid      = request.form.get('CallSid', '')
    speech_result = request.form.get('SpeechResult', '').strip()

    t_request = time.time()
    print(f"===== llm_handle_speech {datetime.now()} q={q_num}/{max_questions} sid={call_sid} =====")
    print(f"SpeechResult: {speech_result}")

    voice_model = _read_from_json(lang, batch, VOICE_ALGO, "config")
    twiml       = VoiceResponse()

    # Load history in main thread (read only — no writes here to avoid SQLite lock)
    history = _load_history(call_sid)
    if history and history[-1].get('a') == '':
        history[-1]['a'] = speech_result or '[no answer]'
    history = _truncate_history(history)

    # Survey complete? Save synchronously — no background work needed
    if q_num >= max_questions:
        _save_history(call_sid, history)
        thanks = _read_from_json(lang, batch, THANKS, "messages")
        twiml.say(thanks, language=_twilio_lang(lang), voice=voice_model)
        twiml.hangup()
        print(f"[TIMING] llm_handle_speech total (survey done): {(time.time()-t_request)*1000:.1f}ms")
        return Response(str(twiml), mimetype='text/xml')

    next_q_num = q_num + 1

    # Fire ALL DB writes + Groq in background — single writer avoids SQLite lock
    event = threading.Event()
    _pending_questions[call_sid] = {"q": None, "error": None, "event": event}

    def _bg_llm(history_snap):
        with app.app_context():
            try:
                _save_history(call_sid, history_snap)           # save filled answer
                next_q = _ask_llm(lang, batch, history_snap, next_q_num, max_questions)
                history_snap.append({"q": next_q, "a": ""})
                _save_history(call_sid, _truncate_history(history_snap))  # save new question
                _pending_questions[call_sid]["q"] = next_q
            except Exception as e:
                print(f"[BG LLM] error: {e}")
                _pending_questions[call_sid]["error"] = str(e)
            finally:
                event.set()

    threading.Thread(target=_bg_llm, args=(history,), daemon=True).start()

    # Respond immediately — filler buys ~500ms while Groq runs in background
    filler = _get_filler(lang)
    twiml.say(filler, language=_twilio_lang(lang), voice=voice_model)
    twiml.redirect(
        f'/llm-next-question?q_num={next_q_num}&lang={lang}'
        f'&name={quote(patient_name)}&batch={batch}&max_q={max_questions}',
        method='POST',
    )

    print(f"[TIMING] llm_handle_speech total: {(time.time()-t_request)*1000:.1f}ms")
    return Response(str(twiml), mimetype='text/xml')


@app.route('/llm-next-question', methods=['POST'])
def llm_next_question():
    lang          = request.args.get('lang') or 'he-IL'
    patient_name  = request.args.get('name') or 'noname'
    batch         = request.args.get('batch') or 'basic'
    q_num         = int(request.args.get('q_num', 2))
    max_questions = int(request.args.get('max_q', 5))
    call_sid      = request.form.get('CallSid', '')

    t0 = time.time()
    print(f"===== llm_next_question {datetime.now()} q={q_num} sid={call_sid} =====")

    try:
        voice_model = _read_from_json(lang, batch, VOICE_ALGO, "config")
        twiml       = VoiceResponse()

        # Wait for background Groq result (should already be done — filler took ~500ms)
        pending = _pending_questions.pop(call_sid, None)
        if pending:
            pending["event"].wait(timeout=3.0)
            next_q = pending.get("q")
            print(f"[TIMING] wait for bg: {(time.time()-t0)*1000:.1f}ms, ready={next_q is not None}")
        else:
            next_q = None
            print(f"[TIMING] no pending entry — falling back to sync Groq call")

        if not next_q:
            # Fallback: background thread failed, call Groq synchronously
            history = _load_history(call_sid)
            next_q  = _ask_llm(lang, batch, history, q_num, max_questions)
            history.append({"q": next_q, "a": ""})
            _save_history(call_sid, _truncate_history(history))
            print(f"[TIMING] sync fallback Groq: {(time.time()-t0)*1000:.1f}ms")

        gather = Gather(
            input='speech',
            speech_timeout='auto',
            language=_twilio_lang(lang),
            speech_model='phone_call',
            action=(
                f'/llm-handle-speech?q_num={q_num}&lang={lang}'
                f'&name={quote(patient_name)}&batch={batch}&max_q={max_questions}'
            ),
            method='POST',
        )
        gather.say(next_q, language=_twilio_lang(lang), voice=voice_model)
        twiml.append(gather)

        sorry = _read_from_json(lang, batch, SORRY_FAILED, "messages")
        twiml.say(sorry, language=_twilio_lang(lang), voice=voice_model)
        twiml.hangup()

    except Exception as e:
        import traceback
        print(f"[ERROR] llm_next_question: {e}")
        traceback.print_exc()
        twiml = VoiceResponse()
        try:
            sorry = _read_from_json(lang, batch, SORRY_FAILED, "messages")
        except Exception:
            sorry = "מצטערים, אירעה שגיאה."
        twiml.say(sorry, language=_twilio_lang(lang))
        twiml.hangup()

    print(f"[TIMING] llm_next_question total: {(time.time()-t0)*1000:.1f}ms")
    return Response(str(twiml), mimetype='text/xml')


@app.route('/llm-recording-callback', methods=['POST'])
def llm_recording_callback():
    """Twilio posts here when the full-call recording is ready."""
    call_sid      = request.form.get('CallSid', '')
    recording_sid = request.form.get('RecordingSid', '')
    recording_url = request.form.get('RecordingUrl', '')

    print(f"===== llm_recording_callback call_sid={call_sid} recording_sid={recording_sid} =====")

    if call_sid and recording_sid and recording_url:
        call_record = Call.query.filter_by(call_sid=call_sid, question_id=0).first()
        if call_record:
            call_record.record_sid    = recording_sid
            call_record.recording_url = recording_url + '.wav'
            db.session.commit()
            print(f"Recording URL saved for call {call_sid}")
        else:
            print(f"No LLM Call record found for call_sid={call_sid}")

    return '', 200


@app.route('/patients/<int:id>/llm-call', methods=['POST'])
@login_required
def patient_llm_call(id):
    patient = Patient.query.filter_by(id=id, deleted=False).first()
    if not patient:
        abort(404)
    if not current_user.is_superuser and patient.therapist_id != current_user.id:
        abort(403)

    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token  = os.environ.get('TWILIO_AUTH_TOKEN')
    client      = Client(account_sid, auth_token)

    try:
        call = client.calls.create(
            to=patient.phone,
            from_=TWILIO_NUMBER,
            url=(
                f'{BASE_URL}/llm-voice?lang={patient.language}'
                f'&name={quote(patient.nickname or patient.name)}'
                f'&batch={patient.batch or "basic"}'
                f'&to_phone={quote(patient.phone)}'
                f'&from_phone={quote(TWILIO_NUMBER)}'
            ),
            record=True,
            recording_channels='dual',
            recording_status_callback=f'{BASE_URL}/llm-recording-callback',
            recording_status_callback_method='POST',
        )

        call_record = Call(
            callSid=call.sid,
            recordSid=None,
            questionId=0,
            recordingUrl=None,
            conversationText='[]',
            patientPhone=patient.phone,
            carrierPhone=TWILIO_NUMBER,
            questionsFile=f"questions_{patient.batch or 'basic'}_{patient.language}.json",
        )
        db.session.add(call_record)
        db.session.commit()

        flash(f'LLM call initiated to {patient.name}. SID: {call.sid}', 'success')
    except Exception as e:
        flash(f'LLM call failed: {str(e)}', 'error')

    return redirect(url_for('patient_list'))
