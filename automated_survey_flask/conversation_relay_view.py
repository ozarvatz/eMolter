"""
========================================================================
  ACTIVE LLM CALL FLOW — Twilio Media Streams + Deepgram + Groq
========================================================================

This module is the canonical implementation of the LLM voice survey.
From now on, every new LLM-call feature MUST be implemented here, NOT in
the deprecated `<Gather>` webhook chain inside `llm_survey_view.py`.

Entry points used by the UI:
  - POST /patients/<id>/relay-call  → `patient_relay_call`
      The "LLM Call" button on the patient list triggers this.
  - GET/POST /llm-relay-voice       → `llm_relay_voice`
      TwiML that returns <Connect><Stream/> pointing at the WebSocket.
  - WebSocket  /ws/media-stream     → `ws_media_stream`
      Bidirectional audio: Twilio <-> us <-> Deepgram (STT) + Groq (LLM).

Helpers borrowed from llm_survey_view.py (do not duplicate them here):
  `_ask_llm`, `_get_filler`, `_vocalize_for_tts`, THANKS, HELLO,
  VOICE_ALGO, SORRY_FAILED.
========================================================================
"""
import os
import json
import time
import base64
from datetime import datetime
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

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
    _ask_llm, _get_filler, _vocalize_for_tts, THANKS, HELLO, VOICE_ALGO, SORRY_FAILED,
)
from .therapist_view import snapshot_patient_to_call

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


def _tts(text, lang='he-IL', gender=None):
    """Generate mulaw 8kHz audio bytes via Google Cloud TTS REST API.
    `gender` is 'male'/'female' — when set with lang='he-IL', gender-ambiguous
    2nd-person suffix words get vocalized so TTS pronounces them correctly."""
    text = _vocalize_for_tts(text, gender, lang)
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

    print(f"[LRV] /llm-relay-voice lang={lang!r} batch={batch!r} "
          f"name={patient_name!r} to={to_phone!r}")

    base_qs = _get_base_questions(lang, batch)
    max_q   = len(base_qs)

    # Twilio does NOT forward query-string params on <Stream url="…">.
    # Custom data has to be passed via <Parameter> children and arrives in
    # the WebSocket `start` event under msg['start']['customParameters'].
    # The URL is now just the endpoint with no query string.
    stream_url_xml = xml_escape(f"{WS_BASE}/ws/media-stream", {'"': '&quot;'})

    def _param(name, value):
        # XML-escape parameter values so unusual names (with &, <, ", etc.)
        # don't break the TwiML document.
        v = xml_escape(str(value), {'"': '&quot;'})
        return f'<Parameter name="{name}" value="{v}"/>'

    parameters_xml = (
        _param('lang',       lang)
        + _param('batch',      batch)
        + _param('name',       patient_name)
        + _param('max_q',      max_q)
        + _param('to_phone',   to_phone)
        + _param('from_phone', from_phone)
    )

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        '<Connect>'
        f'<Stream url="{stream_url_xml}">{parameters_xml}</Stream>'
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

    # Per-call params come from the `start` event's customParameters,
    # which originate from the Patient row in the DB (set by the GUI and
    # passed through patient_relay_call → llm_relay_voice → <Parameter>).
    # No hardcoded fallbacks here on purpose — if a param is missing we
    # want a loud error in the log instead of a silent default that could
    # mask a regression (e.g. Hebrew TTS for an en-US patient).
    lang         = None
    patient_name = None
    batch        = None
    max_q        = None
    to_phone     = None
    from_phone   = None
    dg_lang      = None
    print(f"[MS-WS] /ws/media-stream connected; awaiting start event for params")

    # Shared state (mutable containers for greenlet access)
    state = {
        'stream_sid':  None,
        'call_sid':    None,
        'call_record': None,
        'history':     [],
        'q_num':       0,
        'bot_speaking': False,
        # Pieces of the current utterance — accumulated as Deepgram sends
        # `is_final=true` Results; flushed to on_transcript on UtteranceEnd
        # (or speech_final, whichever arrives first).
        'utterance_parts': [],
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

        cr     = state.get('call_record')
        gender = 'female' if (cr and (cr.patient_gender or '').lower() == 'female') else 'male'

        def _save():
            if cr:
                cr.conversation_text = json.dumps(history, ensure_ascii=False)
                db.session.commit()

        # Survey complete
        if q_num >= max_q:
            state['bot_speaking'] = True
            try:
                thanks = _read(lang, batch, THANKS, "messages")
                thanks_audio = _tts(thanks, lang, gender)
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
            filler_audio = _tts(_get_filler(lang), lang, gender)
            send_audio(filler_audio)
        except Exception as e:
            print(f"[MS] filler TTS error: {e}")

        # LLM call (Groq)
        t0 = time.time()
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
            q_audio = _tts(next_q, lang, gender)
            print(f"[TIMING MS] TTS: {(time.time()-t0)*1000:.1f}ms ({len(q_audio)} bytes)")
            send_audio(q_audio)
        except Exception as e:
            print(f"[MS] question TTS error: {e}")

        state['bot_speaking'] = False

    def _flush_utterance(reason):
        """Concatenate buffered final pieces and hand them to on_transcript.
        Called when Deepgram signals end of utterance (UtteranceEnd, or a
        Results event with speech_final=true). Safe to call when buffer
        is empty — silently no-ops."""
        parts = state['utterance_parts']
        if not parts:
            return
        full = ' '.join(p for p in parts if p).strip()
        state['utterance_parts'] = []
        if not full:
            return
        print(f"[DG] flush ({reason}): {full!r}")
        on_transcript(full)

    def deepgram_receiver():
        """Greenlet: reads events from Deepgram and advances the survey on
        end-of-utterance.

        Event handling:
          - `Results` with `is_final=true` → append `.transcript` to the
            current utterance buffer.
          - `Results` with `speech_final=true` → also flush the buffer (the
            final piece is already appended above).
          - `UtteranceEnd`                  → flush the buffer.
        Every event is logged so we can see exactly what nova-3 emits."""
        with app.app_context():
            while True:
                try:
                    raw = dg_ws[0].recv()
                    # Deepgram closes the WS with an empty frame (raw == '')
                    # or None depending on the peer's behavior. Either way,
                    # treat any falsy payload as a normal close signal so we
                    # don't log a spurious JSON decode error on shutdown.
                    if not raw:
                        print("[DG] recv: empty frame — connection closed")
                        break
                    data = json.loads(raw)
                    ev_type = data.get('type')
                    if ev_type == 'Results':
                        alts = data.get('channel', {}).get('alternatives', [{}])
                        transcript = (alts[0].get('transcript', '') if alts else '').strip()
                        is_final     = bool(data.get('is_final'))
                        speech_final = bool(data.get('speech_final'))
                        print(f"[DG] Results is_final={is_final} "
                              f"speech_final={speech_final} text={transcript!r}")
                        if is_final and transcript:
                            state['utterance_parts'].append(transcript)
                        if speech_final:
                            _flush_utterance('speech_final')
                    elif ev_type == 'UtteranceEnd':
                        print(f"[DG] UtteranceEnd last_word_end={data.get('last_word_end')}")
                        _flush_utterance('UtteranceEnd')
                    elif ev_type == 'SpeechStarted':
                        print(f"[DG] SpeechStarted timestamp={data.get('timestamp')}")
                    else:
                        print(f"[DG] {ev_type or 'unknown'}: {str(data)[:200]}")
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

                # Pull the real per-call params from <Parameter> elements
                # that llm_relay_voice attached to <Stream>. These come from
                # the Patient row in the DB (set via the GUI) — they are
                # the single source of truth for this call.
                cp = msg['start'].get('customParameters', {}) or {}
                lang         = cp.get('lang')
                batch        = cp.get('batch')
                patient_name = cp.get('name')
                to_phone     = cp.get('to_phone') or ''
                from_phone   = cp.get('from_phone') or TWILIO_NUMBER
                try:
                    max_q = int(cp.get('max_q'))
                except (TypeError, ValueError):
                    max_q = None

                # Fail loudly if the DB-derived params didn't arrive — better
                # than silently defaulting to Hebrew and confusing the patient.
                if not lang or not batch or max_q is None:
                    print(f"[MS] start FATAL: missing customParameters — "
                          f"lang={lang!r} batch={batch!r} max_q={max_q!r} "
                          f"name={patient_name!r}. Closing.")
                    try:
                        wsock.close()
                    except Exception:
                        pass
                    return

                dg_lang = _LANG_TO_DG.get(lang)
                if not dg_lang:
                    print(f"[MS] start FATAL: lang={lang!r} has no Deepgram "
                          f"mapping in _LANG_TO_DG. Closing.")
                    try:
                        wsock.close()
                    except Exception:
                        pass
                    return

                print(f"[MS] start stream_sid={state['stream_sid']} "
                      f"call_sid={state['call_sid']} lang={lang!r} batch={batch!r} "
                      f"max_q={max_q} name={patient_name!r} dg_lang={dg_lang!r}")

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

                # Connect to Deepgram.
                # `model=nova-3` is required for Hebrew streaming — the default
                # model (and nova-2) both 400 on `language=he`/`language=he-IL`.
                # nova-3 supports he, de, and en-US, so we pin it for all langs.
                #
                # End-of-utterance detection:
                #   interim_results=true  → required to receive `UtteranceEnd`.
                #   utterance_end_ms=1500 → Deepgram fires `UtteranceEnd` 1.5s
                #                           after the last word. This is our
                #                           primary signal that the patient
                #                           finished speaking.
                #   endpointing=500       → also flags `speech_final` on the
                #                           final Results, as a secondary signal.
                # The receiver buffers `is_final=true` Results and advances
                # the survey on whichever end signal arrives first.
                dg_url = (
                    f"wss://api.deepgram.com/v1/listen"
                    f"?model=nova-3"
                    f"&encoding=mulaw&sample_rate=8000"
                    f"&language={dg_lang}"
                    f"&interim_results=true"
                    f"&endpointing=500"
                    f"&utterance_end_ms=1500"
                )
                try:
                    dg_ws[0] = _ws_lib.create_connection(
                        dg_url,
                        header=[f"Authorization: Token {DEEPGRAM_API_KEY}"],
                    )
                except Exception as e:
                    print(f"[MS] Deepgram connect FAILED ({type(e).__name__}): {e}")
                    print(f"[MS] dg_url was: {dg_url}")
                    try:
                        wsock.close()
                    except Exception:
                        pass
                    return
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

                    gender = 'female' if (call_record.patient_gender or '').lower() == 'female' else 'male'
                    greeting_audio = _tts(greeting, lang, gender)
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

    # Single source of truth: the Patient row from the DB. Everything that
    # follows derives from these values — TwiML URL, Stream URL, WS lang,
    # Call snapshot — no defaults along the way.
    lang  = patient.language
    batch = patient.batch or 'basic'
    print(f"[PRC] patient.id={patient.id} name={patient.name!r} "
          f"language={lang!r} batch={batch!r} gender={patient.gender!r} "
          f"birth_year={patient.birth_year!r} treatment={patient.treatment!r}")

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
                f'?lang={quote(lang)}'
                f'&name={quote(patient.nickname or patient.name)}'
                f'&batch={quote(batch)}'
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
            questionsFile=f"questions_{batch}_{lang}.json",
        )
        # Snapshot patient meta so downstream readers (ws_media_stream,
        # exports, reports) see the values that were true at call time.
        # Without this, cr.patient_gender is None and TTS gender defaults to
        # male regardless of the GUI selection.
        snapshot_patient_to_call(call_record, patient)
        db.session.add(call_record)
        db.session.commit()

        from flask import flash
        flash(f'Relay call initiated to {patient.name}. SID: {call.sid}', 'success')
    except Exception as e:
        from flask import flash
        flash(f'Relay call failed: {str(e)}', 'error')

    from flask import redirect, url_for
    return redirect(url_for('patient_list'))
