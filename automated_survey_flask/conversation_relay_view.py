import os
import json
import time
import base64
from datetime import datetime
from urllib.parse import quote

import requests as http_requests
from flask import request, abort, Response
from flask_login import login_required, current_user

# gevent and websocket-client are always available in production (gunicorn+gevent).
# Imported at module level so tests can patch them.
try:
    import gevent
    import websocket as _ws_lib
except ImportError:  # pragma: no cover — tests mock these
    gevent  = None
    _ws_lib = None

from . import app
from .models import db, Call, Patient
from .llm_survey_view import (
    _ask_llm, _get_filler, THANKS, HELLO, VOICE_ALGO, SORRY_FAILED,
)

TWILIO_NUMBER      = "+17473023043"
BASE_URL           = "https://www.emolter.org:5000"
WS_BASE            = "wss://www.emolter.org:5000"

DEEPGRAM_API_KEY   = os.environ.get('DEEPGRAM_API_KEY', '')
GOOGLE_TTS_API_KEY = os.environ.get('GOOGLE_TTS_API_KEY', '')

# Deepgram language codes (BCP-47 -> Deepgram)
_LANG_TO_DG = {
    'he-IL': 'he',
    'de-DE': 'de',
    'en-US': 'en-US',
}

# Google Cloud TTS voice names
_LANG_TO_VOICE = {
    'he-IL': 'he-IL-Standard-A',
    'de-DE': 'de-DE-Standard-A',
    'en-US': 'en-US-Standard-B',
}


def _read(lang, batch, n, entity):
    from automated_survey_flask.question_service import read_question
    return read_question(lang, batch, n, entity)


def _get_base_questions(lang, batch):
    from automated_survey_flask.question_service import get_questions_list
    return get_questions_list(lang, batch)


def _tts(text, lang='he-IL'):
    """Generate mulaw 8kHz audio bytes via Google Cloud TTS REST API."""
    voice_name = _LANG_TO_VOICE.get(lang, 'en-US-Standard-B')
    url = (
        f"https://texttospeech.googleapis.com/v1/text:synthesize"
        f"?key={GOOGLE_TTS_API_KEY}"
    )
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": lang, "name": voice_name},
        "audioConfig": {"audioEncoding": "MULAW", "sampleRateHertz": 8000},
    }
    resp = http_requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return base64.b64decode(resp.json()['audioContent'])


# ---------------------------------------------------------------------------
# TwiML entry point — returns Media Streams connect instruction
# ---------------------------------------------------------------------------
@app.route('/llm-relay-voice', methods=['GET', 'POST'])
def llm_relay_voice():
    lang         = request.args.get('lang', 'he-IL')
    patient_name = request.args.get('name', 'noname')
    batch        = request.args.get('batch', 'basic')
    to_phone     = request.args.get('to_phone', '')
    from_phone   = request.args.get('from_phone', TWILIO_NUMBER)

    base_qs = _get_base_questions(lang, batch)
    max_q   = len(base_qs)

    stream_url = (
        f"{WS_BASE}/ws/media-stream"
        f"?lang={lang}&name={quote(patient_name)}"
        f"&batch={batch}&max_q={max_q}"
        f"&to_phone={quote(to_phone)}&from_phone={quote(from_phone)}"
    )

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        '<Connect>'
        f'<Stream url="{stream_url}"/>'
        '</Connect>'
        '<Hangup/>'
        '</Response>'
    )
    return Response(twiml, mimetype='text/xml')


# ---------------------------------------------------------------------------
# WebSocket handler — bidirectional Media Streams connection per call
# ---------------------------------------------------------------------------
@app.route('/ws/media-stream')
def ws_media_stream():
    try:
        from geventwebsocket import WebSocketError
    except ImportError:
        WebSocketError = Exception

    wsock = request.environ.get('wsgi.websocket')
    if not wsock:
        abort(400, "Expected WebSocket request.")

    lang         = request.args.get('lang', 'he-IL')
    patient_name = request.args.get('name', 'noname')
    batch        = request.args.get('batch', 'basic')
    max_q        = int(request.args.get('max_q', 5))
    to_phone     = request.args.get('to_phone', '')
    from_phone   = request.args.get('from_phone', TWILIO_NUMBER)

    dg_lang = _LANG_TO_DG.get(lang, 'en-US')

    # Shared state (mutable containers for greenlet access)
    state = {
        'stream_sid':  None,
        'call_sid':    None,
        'call_record': None,
        'history':     [],
        'q_num':       0,
        'bot_speaking': False,
    }
    dg_ws = [None]  # Deepgram WebSocket

    def send_to_twilio(payload):
        try:
            wsock.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            print(f"[MS] send_to_twilio error: {e}")

    def send_audio(audio_bytes):
        """Send mulaw bytes to Twilio in 20ms chunks (160 bytes each)."""
        sid = state['stream_sid']
        if not sid:
            return
        chunk_size = 160  # 20ms at 8kHz mulaw
        for i in range(0, len(audio_bytes), chunk_size):
            chunk = audio_bytes[i:i + chunk_size]
            send_to_twilio({
                "event": "media",
                "streamSid": sid,
                "media": {"payload": base64.b64encode(chunk).decode()},
            })

    def on_transcript(transcript):
        """Called from deepgram_receiver greenlet when a final transcript arrives."""
        if state['bot_speaking']:
            print(f"[MS] ignoring transcript (bot speaking): '{transcript}'")
            return

        q_num = state['q_num']
        print(f"[MS] transcript q{q_num}: '{transcript}'")

        history = state['history']
        if history and history[-1].get('a') == '':
            history[-1]['a'] = transcript or '[no answer]'

        def _save():
            cr = state['call_record']
            if cr:
                cr.conversation_text = json.dumps(history, ensure_ascii=False)
                db.session.commit()

        # Survey complete
        if q_num >= max_q:
            state['bot_speaking'] = True
            try:
                thanks = _read(lang, batch, THANKS, "messages")
                thanks_audio = _tts(thanks, lang)
                _save()
                send_audio(thanks_audio)
                # Pause so Twilio plays the goodbye before we close
                duration = len(thanks_audio) / 8000.0
                gevent.sleep(duration + 0.5)
            except Exception as e:
                print(f"[MS] thanks TTS error: {e}")
            finally:
                try:
                    wsock.close()
                except Exception:
                    pass
            return

        # Generate next question
        next_q_num = q_num + 1
        state['q_num'] = next_q_num
        state['bot_speaking'] = True

        # Pre-cached filler plays immediately
        try:
            filler_audio = _tts(_get_filler(lang), lang)
            send_audio(filler_audio)
        except Exception as e:
            print(f"[MS] filler TTS error: {e}")

        # LLM call (Groq)
        t0 = time.time()
        cr = state.get('call_record')
        gender = 'female' if (cr and (cr.patient_gender or '').lower() == 'female') else 'male'
        try:
            next_q = _ask_llm(lang, batch, history, next_q_num, max_q, gender)
        except Exception as e:
            print(f"[MS] Groq error: {e}")
            try:
                next_q = _read(lang, batch, next_q_num, "questions").format(patient_name)
            except Exception:
                next_q = ""
        print(f"[TIMING MS] Groq: {(time.time()-t0)*1000:.1f}ms → '{next_q[:60]}'")

        history.append({"q": next_q, "a": ""})
        _save()

        # TTS and send question audio
        t0 = time.time()
        try:
            q_audio = _tts(next_q, lang)
            print(f"[TIMING MS] TTS: {(time.time()-t0)*1000:.1f}ms ({len(q_audio)} bytes)")
            send_audio(q_audio)
        except Exception as e:
            print(f"[MS] question TTS error: {e}")

        state['bot_speaking'] = False

    def deepgram_receiver():
        """Greenlet: reads transcripts from Deepgram and calls on_transcript."""
        with app.app_context():
            while True:
                try:
                    raw = dg_ws[0].recv()
                    if raw is None:
                        break
                    data = json.loads(raw)
                    if data.get('type') == 'Results':
                        alts = data.get('channel', {}).get('alternatives', [{}])
                        transcript = (alts[0].get('transcript', '') if alts else '').strip()
                        if data.get('speech_final') and transcript:
                            print(f"[DG] speech_final: '{transcript}'")
                            on_transcript(transcript)
                except Exception as e:
                    print(f"[DG] recv error: {e}")
                    break

    try:
        while True:
            raw = wsock.receive()
            if raw is None:
                break

            msg   = json.loads(raw)
            event = msg.get('event')

            print(f"[MS {datetime.now().strftime('%H:%M:%S')}] {event}")

            if event == 'connected':
                pass

            elif event == 'start':
                state['stream_sid'] = msg['start']['streamSid']
                state['call_sid']   = msg['start']['callSid']
                to_phone   = to_phone or msg['start'].get('customParameters', {}).get('to_phone', '')
                from_phone = from_phone or msg['start'].get('customParameters', {}).get('from_phone', TWILIO_NUMBER)

                print(f"[MS] start stream_sid={state['stream_sid']} call_sid={state['call_sid']}")

                # Create DB record
                call_record = Call.query.filter_by(
                    call_sid=state['call_sid'], question_id=0
                ).first()
                if not call_record:
                    call_record = Call(
                        callSid=state['call_sid'],
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
                state['call_record'] = call_record

                # Connect to Deepgram
                dg_url = (
                    f"wss://api.deepgram.com/v1/listen"
                    f"?encoding=mulaw&sample_rate=8000"
                    f"&language={dg_lang}"
                    f"&interim_results=false"
                    f"&endpointing=500"
                    f"&punctuate=true"
                )
                dg_ws[0] = _ws_lib.create_connection(
                    dg_url,
                    header=[f"Authorization: Token {DEEPGRAM_API_KEY}"],
                )
                gevent.spawn(deepgram_receiver)

                # Send greeting
                state['bot_speaking'] = True
                try:
                    hello    = _read(lang, batch, HELLO, "messages")
                    intro_q  = _read(lang, batch, 1, "questions").format(patient_name)
                    greeting = f"{hello} {intro_q}"

                    state['history'] = [{"q": greeting, "a": ""}]
                    call_record.conversation_text = json.dumps(
                        state['history'], ensure_ascii=False
                    )
                    db.session.commit()
                    state['q_num'] = 1

                    greeting_audio = _tts(greeting, lang)
                    send_audio(greeting_audio)
                except Exception as e:
                    print(f"[MS] greeting error: {e}")
                finally:
                    state['bot_speaking'] = False

                print(f"[MS] greeting sent, q_num=1")

            elif event == 'media':
                if not state['bot_speaking'] and dg_ws[0]:
                    try:
                        payload = base64.b64decode(msg['media']['payload'])
                        dg_ws[0].send_binary(payload)
                    except Exception as e:
                        print(f"[MS] dg send error: {e}")

            elif event == 'stop':
                print(f"[MS] stop")
                break

            elif event == 'mark':
                print(f"[MS] mark: {msg.get('mark', {}).get('name')}")

    except WebSocketError as e:
        print(f"[MS] WebSocketError: {e}")
    except Exception as e:
        import traceback
        print(f"[MS] ERROR: {e}")
        traceback.print_exc()
    finally:
        if dg_ws[0]:
            try:
                dg_ws[0].close()
            except Exception:
                pass
        print(f"[MS] connection closed call_sid={state['call_sid']}")

    return Response('', status=200)


# ---------------------------------------------------------------------------
# Call initiator — uses Media Streams (same endpoint as before)
# ---------------------------------------------------------------------------
@app.route('/patients/<int:id>/relay-call', methods=['POST'])
@login_required
def patient_relay_call(id):
    from twilio.rest import Client
    import os as _os

    patient = Patient.query.filter_by(id=id, deleted=False).first()
    if not patient:
        abort(404)
    if not current_user.is_superuser and patient.therapist_id != current_user.id:
        abort(403)

    client = Client(
        _os.environ.get('TWILIO_ACCOUNT_SID'),
        _os.environ.get('TWILIO_AUTH_TOKEN'),
    )

    try:
        call = client.calls.create(
            to=patient.phone,
            from_=TWILIO_NUMBER,
            url=(
                f'{BASE_URL}/llm-relay-voice'
                f'?lang={patient.language}'
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

        from flask import flash
        flash(f'Relay call initiated to {patient.name}. SID: {call.sid}', 'success')
    except Exception as e:
        from flask import flash
        flash(f'Relay call failed: {str(e)}', 'error')

    from flask import redirect, url_for
    return redirect(url_for('patient_list'))
