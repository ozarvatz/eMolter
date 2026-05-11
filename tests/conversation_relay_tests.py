"""
Tests for the Media Streams + Deepgram + Google TTS conversation relay flow.

Covers:
- /llm-relay-voice TwiML endpoint (returns <Connect><Stream>)
- _tts() helper function
- /patients/<id>/relay-call call initiator
- ws_media_stream WebSocket handler (basic DB + greeting flow)
"""
import json
import base64
import unittest
from unittest.mock import patch, MagicMock, call as mock_call

from tests.base import BaseTest
from automated_survey_flask import app, db
from automated_survey_flask.models import Call, Patient, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64_mulaw(n_bytes=160):
    return base64.b64encode(bytes(n_bytes)).decode()


def _fake_tts_response(n_bytes=800):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {'audioContent': _b64_mulaw(n_bytes)}
    return mock_resp


def _build_fake_wsock(messages):
    """Mock WebSocket that yields messages then None."""
    wsock = MagicMock()
    wsock.receive.side_effect = list(messages) + [None]
    wsock.send = MagicMock()
    wsock.close = MagicMock()
    return wsock


def _build_fake_dg_ws():
    dg = MagicMock()
    dg.recv.return_value = None
    dg.send_binary = MagicMock()
    dg.close = MagicMock()
    return dg


CALL_SID   = 'CA_ws_test_001'
STREAM_SID = 'MZ_ws_test_001'


def _connected():
    return json.dumps({"event": "connected", "protocol": "Call", "version": "1.0"})


def _start():
    return json.dumps({
        "event": "start",
        "start": {
            "streamSid": STREAM_SID,
            "callSid":   CALL_SID,
            "tracks":    ["inbound"],
            "customParameters": {},
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
        "streamSid": STREAM_SID,
    })


def _media():
    return json.dumps({
        "event": "media",
        "media": {"track": "inbound", "chunk": "1", "timestamp": "5", "payload": _b64_mulaw(160)},
        "streamSid": STREAM_SID,
    })


def _stop():
    return json.dumps({"event": "stop", "streamSid": STREAM_SID})


# ---------------------------------------------------------------------------
# Shared patch context for the WebSocket handler
# ---------------------------------------------------------------------------

def _ws_patches(fake_wsock, fake_dg_ws=None):
    """Return a dict of patch targets and their mocks for the WS handler."""
    if fake_dg_ws is None:
        fake_dg_ws = _build_fake_dg_ws()

    mock_ws_lib = MagicMock()
    mock_ws_lib.create_connection.return_value = fake_dg_ws

    mock_gevent = MagicMock()
    mock_gevent.spawn = MagicMock()
    mock_gevent.sleep = MagicMock()

    return {
        'automated_survey_flask.conversation_relay_view._ws_lib':  mock_ws_lib,
        'automated_survey_flask.conversation_relay_view.gevent':   mock_gevent,
    }


# ---------------------------------------------------------------------------
# TwiML endpoint
# ---------------------------------------------------------------------------

class LlmRelayVoiceTwimlTest(BaseTest):

    @patch('automated_survey_flask.conversation_relay_view._get_base_questions',
           return_value=['Q1', 'Q2', 'Q3'])
    def test_returns_stream_twiml(self, _):
        response = self.client.get('/llm-relay-voice?lang=he-IL&name=Alice&batch=basic')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<Stream', response.data)
        self.assertIn(b'/ws/media-stream', response.data)

    @patch('automated_survey_flask.conversation_relay_view._get_base_questions',
           return_value=['Q1', 'Q2', 'Q3'])
    def test_twiml_includes_hangup(self, _):
        response = self.client.get('/llm-relay-voice?lang=he-IL')
        self.assertIn(b'<Hangup', response.data)

    @patch('automated_survey_flask.conversation_relay_view._get_base_questions',
           return_value=['Q1', 'Q2', 'Q3'])
    def test_twiml_includes_lang_and_name(self, _):
        response = self.client.get('/llm-relay-voice?lang=de-DE&name=Hans')
        self.assertIn(b'lang=de-DE', response.data)
        self.assertIn(b'Hans', response.data)

    @patch('automated_survey_flask.conversation_relay_view._get_base_questions',
           return_value=['Q1', 'Q2', 'Q3', 'Q4', 'Q5'])
    def test_twiml_max_q_reflects_question_count(self, _):
        response = self.client.get('/llm-relay-voice?lang=en-US&batch=deep')
        self.assertIn(b'max_q=5', response.data)


# ---------------------------------------------------------------------------
# _tts() helper
# ---------------------------------------------------------------------------

class TtsHelperTest(BaseTest):

    @patch('automated_survey_flask.conversation_relay_view.http_requests')
    def test_calls_google_tts_api(self, mock_req):
        mock_req.post.return_value = _fake_tts_response()

        from automated_survey_flask.conversation_relay_view import _tts
        result = _tts("שלום עולם", 'he-IL')

        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)
        url_arg = mock_req.post.call_args[0][0]
        self.assertIn('texttospeech.googleapis.com', url_arg)

    @patch('automated_survey_flask.conversation_relay_view.http_requests')
    def test_correct_mulaw_encoding_requested(self, mock_req):
        mock_req.post.return_value = _fake_tts_response()

        from automated_survey_flask.conversation_relay_view import _tts
        _tts("hello", 'en-US')

        body = mock_req.post.call_args[1]['json']
        self.assertEqual(body['audioConfig']['audioEncoding'], 'MULAW')
        self.assertEqual(body['audioConfig']['sampleRateHertz'], 8000)

    @patch('automated_survey_flask.conversation_relay_view.http_requests')
    def test_correct_voice_for_language(self, mock_req):
        mock_req.post.return_value = _fake_tts_response()

        from automated_survey_flask.conversation_relay_view import _tts
        _tts("Guten Tag", 'de-DE')

        body = mock_req.post.call_args[1]['json']
        self.assertEqual(body['voice']['name'], 'de-DE-Standard-A')
        self.assertEqual(body['voice']['languageCode'], 'de-DE')

    @patch('automated_survey_flask.conversation_relay_view.http_requests')
    def test_returns_decoded_bytes(self, mock_req):
        raw = b'\xd5\xd5\xd5' * 100
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {'audioContent': base64.b64encode(raw).decode()}
        mock_req.post.return_value = mock_resp

        from automated_survey_flask.conversation_relay_view import _tts
        self.assertEqual(_tts("x", 'en-US'), raw)


# ---------------------------------------------------------------------------
# /patients/<id>/relay-call endpoint
# ---------------------------------------------------------------------------

class PatientRelayCallTest(BaseTest):

    def _make_superuser(self):
        u = User(phone='+972501234567', nickname='Super', is_superuser=True)
        u.set_password('pass')
        db.session.add(u)
        db.session.commit()
        return u

    def _make_patient(self, therapist_id=None):
        p = Patient(
            name='Test Patient',
            phone='+972509999999',
            language='he-IL',
            batch='basic',
        )
        if therapist_id:
            p.therapist_id = therapist_id
        db.session.add(p)
        db.session.commit()
        return p

    def _login(self, phone, pw):
        self.client.post('/login', data={'phone': phone, 'password': pw})

    @patch('twilio.rest.Client')
    def test_creates_db_record(self, mock_cls):
        mock_cls.return_value.calls.create.return_value = MagicMock(sid='CA_relay_1')
        su = self._make_superuser()
        pt = self._make_patient(therapist_id=su.id)
        self._login('+972501234567', 'pass')

        self.client.post(f'/patients/{pt.id}/relay-call')

        record = Call.query.filter_by(call_sid='CA_relay_1').first()
        self.assertIsNotNone(record)
        self.assertEqual(record.question_id, 0)
        self.assertEqual(record.patient_phone, pt.phone)

    @patch('twilio.rest.Client')
    def test_twilio_call_uses_llm_relay_url(self, mock_cls):
        mock_cls.return_value.calls.create.return_value = MagicMock(sid='CA_relay_2')
        su = self._make_superuser()
        pt = self._make_patient(therapist_id=su.id)
        self._login('+972501234567', 'pass')

        self.client.post(f'/patients/{pt.id}/relay-call')

        kwargs = mock_cls.return_value.calls.create.call_args[1]
        self.assertIn('/llm-relay-voice', kwargs['url'])

    def test_requires_login(self):
        pt = self._make_patient()
        response = self.client.post(
            f'/patients/{pt.id}/relay-call', follow_redirects=False
        )
        self.assertIn(response.status_code, [302, 401])

    @patch('twilio.rest.Client')
    def test_therapist_cannot_call_other_therapist_patient(self, mock_cls):
        t1 = User(phone='+972501111111', nickname='T1')
        t1.set_password('p1')
        t2 = User(phone='+972502222222', nickname='T2')
        t2.set_password('p2')
        db.session.add_all([t1, t2])
        db.session.commit()

        pt = self._make_patient(therapist_id=t2.id)
        self._login('+972501111111', 'p1')

        response = self.client.post(f'/patients/{pt.id}/relay-call')
        self.assertIn(response.status_code, [403, 302])
        mock_cls.return_value.calls.create.assert_not_called()


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

class WsMediaStreamHandlerTest(BaseTest):

    def _run_handler(self, wsock, fake_dg=None, lang='he-IL', max_q=3):
        """Invoke ws_media_stream with a fake wsgi.websocket environ."""
        if fake_dg is None:
            fake_dg = _build_fake_dg_ws()

        mock_ws_lib = MagicMock()
        mock_ws_lib.create_connection.return_value = fake_dg
        mock_gevent = MagicMock()
        mock_gevent.spawn = MagicMock()
        mock_gevent.sleep = MagicMock()

        with patch('automated_survey_flask.conversation_relay_view._ws_lib', mock_ws_lib), \
             patch('automated_survey_flask.conversation_relay_view.gevent', mock_gevent), \
             patch('automated_survey_flask.conversation_relay_view._read',
                   side_effect=lambda l, b, n, e: f"[{e}:{n}]"), \
             patch('automated_survey_flask.conversation_relay_view._get_base_questions',
                   return_value=['Q1', 'Q2', 'Q3']), \
             patch('automated_survey_flask.conversation_relay_view._tts',
                   return_value=bytes(160)):

            with app.test_request_context(
                f'/ws/media-stream?lang={lang}&name=Alice&batch=basic&max_q={max_q}',
                environ_base={'wsgi.websocket': wsock},
            ):
                from automated_survey_flask.conversation_relay_view import ws_media_stream
                ws_media_stream()

        return mock_ws_lib, mock_gevent

    def test_no_websocket_returns_400(self):
        response = self.client.get('/ws/media-stream?lang=he-IL')
        self.assertEqual(response.status_code, 400)

    def test_start_event_creates_call_record(self):
        wsock = _build_fake_wsock([_connected(), _start(), _stop()])
        self._run_handler(wsock)

        record = Call.query.filter_by(call_sid=CALL_SID).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.question_id, 0)

    def test_start_event_stores_greeting_in_history(self):
        wsock = _build_fake_wsock([_connected(), _start(), _stop()])
        self._run_handler(wsock)

        record = Call.query.filter_by(call_sid=CALL_SID).first()
        history = json.loads(record.conversation_text)
        self.assertEqual(len(history), 1)
        self.assertIn('q', history[0])
        self.assertEqual(history[0]['a'], '')

    def test_start_event_sends_greeting_audio_to_twilio(self):
        wsock = _build_fake_wsock([_connected(), _start(), _stop()])
        self._run_handler(wsock)

        sent = [json.loads(c[0][0]) for c in wsock.send.call_args_list if c[0]]
        media_events = [p for p in sent if p.get('event') == 'media']
        self.assertGreater(len(media_events), 0)

    def test_deepgram_connected_on_start(self):
        wsock = _build_fake_wsock([_connected(), _start(), _stop()])
        mock_ws_lib, _ = self._run_handler(wsock)
        mock_ws_lib.create_connection.assert_called_once()
        url = mock_ws_lib.create_connection.call_args[0][0]
        self.assertIn('api.deepgram.com', url)

    def test_deepgram_receiver_greenlet_spawned(self):
        wsock = _build_fake_wsock([_connected(), _start(), _stop()])
        _, mock_gevent = self._run_handler(wsock)
        mock_gevent.spawn.assert_called_once()

    def test_existing_call_record_not_duplicated(self):
        # Pre-create a Call record for this call_sid
        with app.app_context():
            existing = Call(
                callSid=CALL_SID,
                recordSid=None,
                questionId=0,
                recordingUrl=None,
                conversationText='[]',
                patientPhone='',
                carrierPhone='',
                questionsFile='questions_basic_he-IL.json',
            )
            db.session.add(existing)
            db.session.commit()

        wsock = _build_fake_wsock([_connected(), _start(), _stop()])
        self._run_handler(wsock)

        records = Call.query.filter_by(call_sid=CALL_SID).all()
        self.assertEqual(len(records), 1)

    def test_media_event_forwarded_to_deepgram(self):
        wsock = _build_fake_wsock([_connected(), _start(), _media(), _stop()])
        fake_dg = _build_fake_dg_ws()
        self._run_handler(wsock, fake_dg=fake_dg)
        # At least one send_binary call means audio was forwarded
        self.assertGreater(fake_dg.send_binary.call_count, 0)

    def test_deepgram_closed_on_handler_exit(self):
        wsock = _build_fake_wsock([_connected(), _start(), _stop()])
        fake_dg = _build_fake_dg_ws()
        self._run_handler(wsock, fake_dg=fake_dg)
        fake_dg.close.assert_called()


if __name__ == '__main__':
    unittest.main()
