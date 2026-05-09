import json
import time
from datetime import datetime
from urllib.parse import quote

from flask import request, abort, Response
from flask_login import login_required, current_user

from . import app
from .models import db, Call, Patient
from .llm_survey_view import ask_llm_stream, _twilio_lang, _get_filler, THANKS, HELLO, VOICE_ALGO, SORRY_FAILED

TWILIO_NUMBER = "+17473023043"
BASE_URL      = "https://www.emolter.org:5000"
WS_BASE       = "wss://www.emolter.org:5000"


def _read(lang, batch, n, entity):
    from automated_survey_flask.question_service import read_question
    return read_question(lang, batch, n, entity)


def _get_base_questions(lang, batch):
    from automated_survey_flask.question_service import get_questions_list
    return get_questions_list(lang, batch)


# ---------------------------------------------------------------------------
# TwiML entry point — returns ConversationRelay connect instruction
# ---------------------------------------------------------------------------
@app.route('/llm-relay-voice', methods=['GET', 'POST'])
def llm_relay_voice():
    lang         = request.args.get('lang', 'he-IL')
    patient_name = request.args.get('name', 'noname')
    batch        = request.args.get('batch', 'basic')
    to_phone     = request.args.get('to_phone', '')
    from_phone   = request.args.get('from_phone', TWILIO_NUMBER)

    base_qs     = _get_base_questions(lang, batch)
    max_q       = len(base_qs)
    voice_model = _read(lang, batch, VOICE_ALGO, "config")

    ws_url = (
        f"{WS_BASE}/ws/conversation"
        f"?lang={lang}&name={quote(patient_name)}"
        f"&batch={batch}&max_q={max_q}"
        f"&to_phone={quote(to_phone)}&from_phone={quote(from_phone)}"
    )

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Connect>'
        f'<ConversationRelay url="{ws_url}"'
        f' language="{lang}"'
        f' voice="{voice_model}"'
        f' dtmfDetection="false"'
        f' interruptByDtmf="false"'
        f' interruptionThreshold="100"'
        f' speechTimeout="1000"/>'
        f'</Connect>'
        '</Response>'
    )
    return Response(twiml, mimetype='text/xml')


# ---------------------------------------------------------------------------
# WebSocket handler — one persistent connection per call
# ---------------------------------------------------------------------------
@app.route('/ws/conversation')
def ws_conversation():
    from geventwebsocket import WebSocketError

    wsock = request.environ.get('wsgi.websocket')
    if not wsock:
        abort(400, "Expected WebSocket request.")

    lang         = request.args.get('lang', 'he-IL')
    patient_name = request.args.get('name', 'noname')
    batch        = request.args.get('batch', 'basic')
    max_q        = int(request.args.get('max_q', 5))
    to_phone     = request.args.get('to_phone', '')
    from_phone   = request.args.get('from_phone', TWILIO_NUMBER)

    call_sid    = None
    history     = []
    q_num       = 0
    call_record = None

    def send(payload):
        wsock.send(json.dumps(payload, ensure_ascii=False))

    def stream_next_question(hist, next_q_num):
        """Stream Groq tokens directly to Twilio. Returns full generated text."""
        full_text = ""
        for token in ask_llm_stream(lang, batch, hist, next_q_num, max_q):
            full_text += token
            send({"type": "text", "token": token, "last": False})
        send({"type": "text", "token": "", "last": True})
        return full_text

    def save_history():
        if call_record:
            call_record.conversation_text = json.dumps(history, ensure_ascii=False)
            db.session.commit()

    try:
        while True:
            raw = wsock.receive()
            if raw is None:
                break

            msg      = json.loads(raw)
            msg_type = msg.get('type')
            print(f"[WS {datetime.now()}] {msg_type}: {str(msg)[:150]}")

            # ------------------------------------------------------------------
            if msg_type == 'setup':
                call_sid   = msg.get('callSid', '')
                to_phone   = to_phone or msg.get('to', '')
                from_phone = from_phone or msg.get('from', '')

                call_record = Call.query.filter_by(call_sid=call_sid, question_id=0).first()
                if not call_record:
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

                hello   = _read(lang, batch, HELLO, "messages")
                intro_q = _read(lang, batch, 1, "questions").format(patient_name)
                greeting = f"{hello} {intro_q}"

                history = [{"q": greeting, "a": ""}]
                save_history()
                q_num = 1

                send({"type": "text", "token": greeting, "last": True})
                print(f"[WS] greeting sent, q_num={q_num}")

            # ------------------------------------------------------------------
            elif msg_type == 'prompt':
                answer = msg.get('voicePrompt', '').strip()
                t0 = time.time()
                print(f"[WS] q{q_num} answer: '{answer}' (lang={msg.get('lang')})")

                if history and history[-1].get('a') == '':
                    history[-1]['a'] = answer or '[no answer]'

                if q_num >= max_q:
                    thanks = _read(lang, batch, THANKS, "messages")
                    send({"type": "text", "token": thanks, "last": True})
                    save_history()
                    send({"type": "end"})
                    print(f"[WS] survey complete")
                    break

                q_num += 1
                send({"type": "text", "token": _get_filler(lang) + " ", "last": False})
                next_q = stream_next_question(history, q_num)
                print(f"[TIMING] stream q{q_num}: {(time.time()-t0)*1000:.1f}ms → '{next_q[:60]}'")

                history.append({"q": next_q, "a": ""})
                save_history()

            # ------------------------------------------------------------------
            elif msg_type == 'interrupt':
                print(f"[WS] barge-in at q{q_num}: '{msg.get('utteranceUntilInterrupt','')}'")

            elif msg_type == 'dtmf':
                print(f"[WS] dtmf: {msg.get('digit')}")

    except WebSocketError as e:
        print(f"[WS] WebSocketError: {e}")
    except Exception as e:
        import traceback
        print(f"[WS] ERROR: {e}")
        traceback.print_exc()
    finally:
        print(f"[WS] connection closed call_sid={call_sid}")

    return Response('', status=200)


# ---------------------------------------------------------------------------
# Call initiator — like patient_llm_call but uses ConversationRelay
# ---------------------------------------------------------------------------
@app.route('/patients/<int:id>/relay-call', methods=['POST'])
@login_required
def patient_relay_call(id):
    from twilio.rest import Client
    import os

    patient = Patient.query.filter_by(id=id, deleted=False).first()
    if not patient:
        abort(404)
    if not current_user.is_superuser and patient.therapist_id != current_user.id:
        abort(403)

    client = Client(os.environ.get('TWILIO_ACCOUNT_SID'), os.environ.get('TWILIO_AUTH_TOKEN'))

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

        from flask import flash, redirect, url_for
        flash(f'Relay call initiated to {patient.name}. SID: {call.sid}', 'success')
    except Exception as e:
        from flask import flash, redirect, url_for
        flash(f'Relay call failed: {str(e)}', 'error')

    from flask import redirect, url_for
    return redirect(url_for('patient_list'))
