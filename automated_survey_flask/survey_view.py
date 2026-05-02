from . import app
from .models import Survey
from flask import url_for, session, request, send_from_directory, Response, abort
# from twilio.twiml.voice_response import VoiceResponse, Response, Gather, Say, Start, Transcription
from twilio.twiml.voice_response import VoiceResponse, Gather, Say, Start, Transcription
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import os
from pathlib import Path
import json
from urllib.parse import quote
from automated_survey_flask.models import db, Call
from datetime import datetime
import re
import time
import uuid
import threading

# from . import csrf
# from flask_wtf.csrf import CSRFProtect

#Constants
SORRY_FAILED    = 1
YOU_SAID        = 2
THANKS          = 3
HELLO           = 4
#CONFIG
VOICE_ALGO      = 1

#PACH
# BASE_URL        = "https://expectative-refugio-bizarrely.ngrok-free.dev"

# 1. Access the exported environment variables
account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
DB_FILE = 'links_db.json'
EXPORT_DIR = os.path.join(app.static_folder, 'exports')
TTL_SECONDS = 6000 # 10 minutes 

def _twilio_lang(lang):
    """Map language codes to Twilio-compatible codes. Google TTS uses iw-IL for Hebrew."""
    return 'iw-IL' if lang == 'he-IL' else lang


phones_str = os.environ.get('ALLOWED_PHONES', '')

ALLOWED_PHONES = [p.strip() for p in phones_str.split(',') if p.strip()]
# 2. Safety check: Ensure the variables aren't empty
if not account_sid or not auth_token:
    raise ValueError("Missing Twilio credentials. Did you remember to 'export' them in this terminal session?")

# 3. Initialize the Client
client = Client(account_sid, auth_token)

"""
Recommended Voices for a Strong German Accent:
Voice Engine	Gender	Style	TwiML Name
Google Wavenet	Male	Professional/Deep	Google.de-DE-Wavenet-B
Amazon Polly	Female	Natural/Clear	Polly.Vicki-Neural
Google Standard	Female	Clean/Direct	Google.de-DE-Standard-F
"""
@app.route('/voice', methods=['GET', 'POST'])
def voice_survey():
    lang = request.args.get('lang') or session.get('lang') or 'he-IL'
    patientName = request.args.get('name') or session.get('name') or 'noname'
    batch = request.args.get('batch') or session.get('batch') or 'basic'
    current_questionId = int(request.args.get('questionId')) or int(session.get('questionId')) or 1
    call_sid = request.values.get('CallSid')

    from_phone = request.args.get('from_phone')
    to_phone = request.args.get('to_phone')
    print(f'++++++++++++++voice {datetime.now()} ++++++++++++++')
    print(f"voice dtae time {datetime.now()}")
    print(f"param lunguage = {lang}")
    print(f"param patient name = {patientName}")
    print(f"param batch = {batch}")
    print(f"param current questionID = {current_questionId}")
    print(f'from_phone = {from_phone}, to_phone = {to_phone}')
    """TwiML endpoint that asks the first question using Speech Recognition."""
    response = VoiceResponse()
    
    print(f"*******before {datetime.now()} ******")
    voice_model = read_question_from_json(lang, batch, VOICE_ALGO, "config")
    print(f"voice_model = {voice_model}")
    print(f"call sid: {call_sid}")

    current_question_txt = ""
    try:
        current_question_txt = read_question_from_json(lang, batch, current_questionId, "questions")
        print(f"first questionId: {current_questionId}")
        if 1 == current_questionId:
            current_question_txt = current_question_txt.format(patientName)
        print(f"    question: {current_question_txt}")
    except Exception as e:
        print(f"error in read file {e}")

    gather = Gather(
        input='speech',
        speech_timeout='auto',
        language=_twilio_lang(lang),
        speech_model='phone_call',
        action=f'/handle-speech?lang={lang}&name={quote(patientName)}&batch={batch}&questionId={current_questionId}',
        method='POST',
    )
    print(f"[TWIML] voice_survey: lang={lang}→{_twilio_lang(lang)}, model=phone_call, q={current_questionId}")
    if 1 == current_questionId:
        hello_text_msg = read_question_from_json(lang, batch, HELLO, "messages")
        gather.say(hello_text_msg, language=_twilio_lang(lang), voice=voice_model)
    gather.say(current_question_txt, language=_twilio_lang(lang), voice=voice_model)
    response.append(gather)

    sorry_failed = read_question_from_json(lang, batch, SORRY_FAILED, "messages")
    response.say(sorry_failed, language=_twilio_lang(lang), voice=voice_model)
    response.hangup()

    return Response(str(response), mimetype='text/xml')

def read_question_from_json(lang, batch, n, entity):
    from automated_survey_flask.question_service import read_question
    return read_question(lang, batch, n, entity)

@app.route('/message', methods=['GET', 'POST'])
def sms_survey():
    response = MessagingResponse()

    survey = Survey.query.first()
    if survey_error(survey, response.message):
        return str(response)

    if 'question_id' in session:
        response.redirect(url_for('answer', question_id=session['question_id']))
    else:
        welcome_user(survey, response.message)
        redirect_to_first_question(response, survey)
    # return str(response)
    return Response(str(response), mimetype='text/xml')


@app.route("/handle-speech", methods=['POST', 'GET'])
def handle_speech():
    lang = request.args.get('lang') or session.get('lang') or 'he-IL'
    batch = request.args.get('batch') or session.get('batch') or 'basic'
    patientName = request.args.get('name') or session.get('name') or 'noname'
    current_questionId = int(request.args.get('questionId')) or int(session.get('questionId')) or 2
    speech_result = request.form.get('SpeechResult', '').strip()
    call_sid = request.form.get('CallSid')
    patient_phone = fixPhoneNumber(request.form.get('To', ''))
    carrier_phone = fixPhoneNumber(request.form.get('From', ''))

    t_request = time.time()
    print(f"############# handle_speech {datetime.now()}##############")
    print(f"lang={lang}, questionId={current_questionId}, call_sid={call_sid}")
    print(f"SpeechResult: {speech_result}")
    print(f"[TIMING] request parsing: {(time.time()-t_request)*1000:.1f}ms")

    t0 = time.time()
    call_snippet = Call.query.filter_by(call_sid=call_sid, question_id=1).first()
    print(f"[TIMING] DB read call: {(time.time()-t0)*1000:.1f}ms")

    t0 = time.time()
    questions_file = f"questions_{batch}_{lang}.json"
    try:
        question_text = read_question_from_json(lang, batch, current_questionId, "questions")
        if current_questionId == 1:
            question_text = question_text.format(patientName)
    except Exception:
        question_text = f"Question {current_questionId}"
    print(f"[TIMING] read question from json: {(time.time()-t0)*1000:.1f}ms")

    qa_entry = f"Q{current_questionId}: {question_text}\nA{current_questionId}: {speech_result}"
    t0 = time.time()
    if call_snippet:
        existing = call_snippet.conversation_text or ""
        call_snippet.conversation_text = (existing + f"\n{qa_entry}").strip()
        call_snippet.questions_file = questions_file
        if patient_phone:
            call_snippet.patient_phone = patient_phone
        if carrier_phone:
            call_snippet.carrier_phone = carrier_phone
    else:
        call_snippet = Call(
            callSid=call_sid,
            recordSid=None,
            questionId=1,
            recordingUrl=None,
            conversationText=qa_entry,
            patientPhone=patient_phone,
            carrierPhone=carrier_phone,
            questionsFile=questions_file,
        )
        db.session.add(call_snippet)
    print(f"[TIMING] DB object update: {(time.time()-t0)*1000:.1f}ms")

    t0 = time.time()
    db.session.commit()
    print(f"[TIMING] DB commit: {(time.time()-t0)*1000:.1f}ms")

    t0 = time.time()
    response = VoiceResponse()
    voice_model = read_question_from_json(lang, batch, VOICE_ALGO, "config")
    print(f"[TIMING] read voice model from json: {(time.time()-t0)*1000:.1f}ms")

    t0 = time.time()
    next_questionId = current_questionId + 1
    try:
        read_question_from_json(lang, batch, next_questionId, "questions")
        print(f"[TIMING] read next question from json: {(time.time()-t0)*1000:.1f}ms")
        next_url = f"/voice?lang={lang}&questionId={next_questionId}&name={quote(patientName)}&batch={batch}"
        print(f"new Url: {next_url}")
        response.redirect(next_url)
    except Exception as e:
        print(f"[TIMING] read next question (end of survey): {(time.time()-t0)*1000:.1f}ms")
        print(e)
        thanks = read_question_from_json(lang, batch, THANKS, "messages")
        response.say(
            thanks,
            language=_twilio_lang(lang),
            voice=voice_model
        )
        response.hangup()
        full_conversation = call_snippet.conversation_text if call_snippet else speech_result
        if full_conversation:
            threading.Thread(
                target=send_survey_summary,
                args=(carrier_phone, patient_phone, full_conversation),
                daemon=True,
            ).start()

    print(f"[TIMING] handle_speech TOTAL: {(time.time()-t_request)*1000:.1f}ms")
    return Response(str(response), mimetype='text/xml')



@app.route('/handle-realtime-text', methods=['POST'])
def handle_realtime_text():
    # patient_phone = request.form.get('To','') 
    # twillio_phone = request.form.get('From','') 
    patient_phone = request.values.get('to_phone')
    twillio_phone = request.values.get('from_phone')
    question_id = request.values.get('questionId')
    batch = request.values.get('batch')
    lang = request.values.get('lang')
    call_sid = request.values.get('CallSid')

    t_request = time.time()
    print(f"=============handle-realtime-text {datetime.now()} ============")
    print(f"from: {twillio_phone}, to: {patient_phone}")
    print(f'call sid: {call_sid}, question Id: {question_id}')

    # Get the raw string data
    raw_data = request.form.get('TranscriptionData') # Verify the field name from Twilio
    print(f"THE DATA :::::::::::::: {raw_data}")
    try:
        # Convert the string to a dictionary
        transcription_data = json.loads(raw_data)
        speech_text = transcription_data.get('transcript')
        if speech_text:
            message_sid = send_survey_summary(twillio_phone, patient_phone, speech_text)
            patient_phone = fixPhoneNumber(patient_phone)
            twillio_phone = fixPhoneNumber(twillio_phone)
            print(f"============twilio: {twillio_phone}, patient: {patient_phone}, YOU SAY - TEXT {speech_text}")
            t0 = time.time()
            call_snippet = Call.query.filter_by(call_sid=call_sid)\
                .order_by(Call.question_id.desc())\
                .first()
            print(f"[TIMING] DB read call (realtime): {(time.time()-t0)*1000:.1f}ms")

            if call_snippet:
                call_snippet.conversation_text = (call_snippet.conversation_text or "") + f" {speech_text}"
                if patient_phone and len(patient_phone) > 1:
                    call_snippet.patient_phone = patient_phone
                if twillio_phone and len(twillio_phone) > 1:
                    call_snippet.carrier_phone = twillio_phone
                question_id = call_snippet.question_id
                print(f"update call row, call sid: {call_sid}, question Id: {question_id}")
            else:
                call_record = Call(
                    callSid=call_sid,
                    recordSid=None, # To be updated by handle-speech
                    questionId=question_id,
                    recordingUrl=None,
                    conversationText=speech_text,
                    patientPhone=patient_phone,
                    carrierPhone=twillio_phone,
                    questionsFile=f"questions_{batch}_{lang}.json",
                )
                db.session.add(call_record)

            t0 = time.time()
            db.session.commit()
            print(f"[TIMING] DB write call (realtime): {(time.time()-t0)*1000:.1f}ms")

    except (json.JSONDecodeError, AttributeError, TypeError):
        # Fallback if it's already a dict or if it's empty
        speech_text = raw_data

    print(f"[TIMING] handle_realtime_text total: {(time.time()-t_request)*1000:.1f}ms")
    return '', 200

def send_survey_summary(from_number, to_number, questions_or_answers):
    # Format the body of the message
    # WhatsApp supports Hebrew characters and RTL (Right-to-Left) natively.
    if not is_basic_valid(from_number):
        from_number = from_number.strip()
        from_number = '+' + from_number
    if not is_basic_valid(to_number):
        to_number = to_number.strip()
        to_number = '+' + to_number
        
    if is_basic_valid(from_number) and is_basic_valid(to_number):    
        message = client.messages.create(
            from_=f'{from_number}',  # Your Twilio WhatsApp Number
            body=questions_or_answers,
            to=f'{to_number}'      # The participant's number in E.164 format
        )
        return message.sid
    else:
        print(f"numbers are not valid - from_number {from_number}, to_number {to_number}")


    return None

@app.route('/non-llm-recording-callback', methods=['POST'])
def non_llm_recording_callback():
    """Twilio posts here when the full-call recording is ready (non-LLM path)."""
    call_sid = request.form.get('CallSid', '')
    recording_sid = request.form.get('RecordingSid', '')
    recording_url = request.form.get('RecordingUrl', '')
    print(f"===== non_llm_recording_callback call_sid={call_sid} =====")
    if call_sid and recording_sid and recording_url:
        call_record = Call.query.filter_by(call_sid=call_sid, question_id=1).first()
        if call_record and not call_record.recording_url:
            call_record.record_sid = recording_sid
            call_record.recording_url = recording_url + '.wav'
            db.session.commit()
            print(f"Recording URL saved for non-LLM call {call_sid}")
    return '', 200


@app.route('/send-report/<sender>/<to>', methods=['GET', 'POST'])
def send_whatsapp_report(sender, to):
    # 1. Generate the CSV file (using the logic we discussed)
    # For this example, let's assume it's saved in a 'static/exports' folder
    filename = "prosody_report.csv"
    filepath = os.path.join(EXPORT_DIR, filename)
    unique_id = str(uuid.uuid4())
    ldb = load_db()
    ldb[unique_id] = {
        "filename": filename,
        "created_at": time.time()
    }
    save_db(ldb)

    # [Insert your Pandas Export Logic here to save to filepath]

    # 2. Define the public URL for the file
    # Replace with your actual domain or ngrok URL
    base_url = "https://www.emolter.org:5000"
    formatted_from = f"whatsapp:+{sender}" if not sender.startswith('whatsapp:') else sender
    formatted_to = f"whatsapp:+{to}" if not to.startswith('whatsapp:') else to
    secret_url = f"{base_url}/download/{unique_id}"
    # 3. Send via WhatsApp
    message = client.messages.create(
        from_=f'{formatted_from}', # Twilio Sandbox number
        to=f'{formatted_to}',  # Your WhatsApp number
        body=f"Your eMolter Report is ready. This link expires in 10 minutes: {secret_url}",
    )

    return f"Report sent! unique_id: {unique_id}, SID: {message.sid}"

# Assuming you have the get_allowed_phones() function we wrote earlier
@app.route("/whatsapp-webhook", methods=['POST'])
def whatsapp_reply():
    sender_phone = request.values.get('From', '').replace('whatsapp:+', '')
    incoming_msg = request.values.get('Body', '').lower()
    base_url = "https://www.emolter.org:5000"
    allowed_list = ALLOWED_PHONES #get_allowed_phones() # From your .bashrc environment variable
    
    resp = MessagingResponse()
    
    # SECURITY CHECK
    if sender_phone not in allowed_list:
        # We stay silent or send a polite "Not Authorized"
        resp.message("Sorry, this number is not authorized to receive eMolter reports.")
        return str(resp)

    # If authorized, proceed with generating the unique link
    if "report" in incoming_msg:
        # ... generate unique_id, save to DB, and send link ...
        unique_id = str(uuid.uuid4())
        # (Rest of your logic)
        resp.message(f"Authorized. Your report link: {base_url}/download/{unique_id}")
    
    return str(resp)

@app.route('/download/<link_id>')
def secure_download(link_id):
    ldb = load_db()
    print(f"ldb = {json.dumps(ldb)}")
    if link_id not in ldb:
        return "Invalid Link.", 404

    link_data = ldb[link_id]
    
    # --- TTL CHECK ---
    elapsed_time = time.time() - link_data['created_at']
    if elapsed_time > TTL_SECONDS:
        # Optional: Delete expired entry from DB
        del ldb[link_id]
        save_db(ldb)
        return "This link has expired.", 403

    return send_from_directory(EXPORT_DIR, link_data['filename'])



def is_basic_valid(phone_number):
    # Remove any accidental spaces
    phone = phone_number.strip()
    
    # Check if starts with + and has enough digits (min 10 for Israel)
    if phone.startswith('+') and len(phone) >= 10 and phone[1:].isdigit():
        return True
    return False
##########################

def redirect_to_first_question(response, survey):
    first_question = survey.questions.order_by('id').first()
    first_question_url = url_for('question', question_id=first_question.id)
    response.redirect(url=first_question_url, method='GET')


def welcome_user(survey, send_function):
    welcome_text = 'Welcome to the %s' % survey.title
    send_function(welcome_text)

def fixPhoneNumber(phone_number):
    number = re.sub(r'\D', '', phone_number)
    if not number: 
        return number
    return f"+{number}"

def survey_error(survey, send_function):
    if not survey:
        send_function('Sorry, but there are no surveys to be answered.')
        return True
    elif not survey.has_questions:
        send_function('Sorry, there are no questions for this survey.')
        return True
    return False

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, 'r') as f:
        return json.load(f)
    
def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f)