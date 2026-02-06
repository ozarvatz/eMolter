from . import app
from .models import Survey
from flask import url_for, session, request
from twilio.twiml.voice_response import VoiceResponse, Gather, Say
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import os
from pathlib import Path
import json
from urllib.parse import quote
from twilio.twiml.voice_response import VoiceResponse, Gather, Say, Start, Transcription


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
    current_questionId = int(request.args.get('questionId', 1)) or int(session.get('questionId')) or 1
    call_sid = request.values.get('CallSid')

    from_phone = request.args.get('from_phone')
    to_phone = request.args.get('to_phone')
    
    print(f"param lunguage = {lang}")
    print(f"param patient name = {patientName}")
    print(f"param batch = {batch}")
    print(f"param current questionID = {current_questionId}")
    print(f'from_phone = {from_phone}, to_phone = {to_phone}')
    """TwiML endpoint that asks the first question using Speech Recognition."""
    response = VoiceResponse()
    
    # 1. Start a "Listener" that understands Hebrew/German
    # This runs in the background while the recording happens
    start = Start()
    start.transcription(
        language_code=lang, 
        status_callback_url=f'http://{request.host}/handle-realtime-text?questionId={current_questionId}&lang={lang}&callSid={call_sid}&from_phone={from_phone}&to_phone={to_phone}'
    )
    response.append(start)
    
    # gather = Gather(
    #     input='speech',
    #     speech_timeout='auto', # 'auto' is faster than a fixed '2'
    #     language=f'{lang}',
    #     action=f'/handle-speech?lang={lang}&name={patientName}&batch={batch}&questionId={current_questionId}',
    #     method='POST',
    #     # This tells Twilio to record the speech but NOT wait for transcription
    #     #record='record-from-answer'
    #     # This creates a separate recording for JUST this gather
    #     recording_track='outbound_track',
    #     enhanced=True,
    # )
    # response.append(gather)
    print("*******before******")
    voice_model = read_question_from_json(lang, batch, VOICE_ALGO, "config")
    print(f"voice_model = {voice_model}")
    print(f"call sid: {call_sid}")

    # Ask the first question
    # gather.say("Welcome to the automated survey. How is your day going so far?")
    try:
        current_question_txt = read_question_from_json(lang, batch, current_questionId, "questions")
        print(f"first questionId: {current_questionId}")
        if 1 == current_questionId:
            current_question_txt = current_question_txt.format(patientName)
            hello_text_msg = read_question_from_json(lang, batch, HELLO, "messages")
            #gather.say(
            response.say(
                hello_text_msg,
                language=f'{lang}',
                voice=voice_model #'Google.he-IL-Standard-A'
            )
        print(f"first question: {current_question_txt}")
    except Exception as e:
        print(f"error in read file {e}")
    
    
    
    #gather.say(
    response.say(
        current_question_txt, 
        language=f'{lang}', 
        voice=voice_model #'Google.he-IL-Standard-A'
        # hints='שלום, להתראות, סיום, הכל טוב, גרוע, בסדר, מצוין', # Add Hebrew hints here
        # speech_model='experimental_conversations' # Try this model
    )
    
    
    base_url = request.url_root.rstrip('/')
    base_url = base_url.replace("http://", "https://")
    print(f'{base_url}/handle-transcription?questionId={current_questionId}&callSid={call_sid}')
    response.record(
        action=f'/handle-speech?lang={lang}&name={quote(patientName)}&batch={batch}&questionId={current_questionId}',
        method='POST',
        play_beep=True,
        timeout=2,           # Stops recording after 2 seconds of silence
        # max_length=15,       # Limits the answer length
        transcribe=False,     # KEY: Disabling this removes the 15-second delay
        # language='he-IL',
        # Twilio will send the text here whenever it is ready
        #transcription_callback=f'{base_url}/handle-transcription?questionId={current_questionId}&callSid={call_sid}'
        #transcription_czallback=f'https://expectative-refugio-bizarrely.ngrok-free.dev/handle-transcription?questionId={current_questionId}',
        # transcribe_callback=f'http://188.166.110.236:5000/handle-transcription?questionId={current_questionId}',
    )
    # response.record(
    #     action=f'/handle-speech?lang={lang}&name={patientName}&batch={batch}&questionId={current_questionId}',
    #     method='POST',
    #     play_beep=True,
    #     timeout=3,      # Seconds of silence before stopping
    #     transcribe=True # This tells Twilio to turn the audio into text
    # )

    sorry_failed = read_question_from_json(lang, batch, SORRY_FAILED, "messages")
    # response.append(gather)
    response.say(
        sorry_failed,
        language=f'{lang}',
        voice=voice_model #'Google.he-IL-Standard-A'  
    )
    response.hangup()
    
    return str(response)

def read_question_from_json(lang, batch, n, entity):
    app_root = Path(__file__).parent.resolve()
    json_path = f"questions_{batch}_{lang}.json"
    
    try:
        with open(json_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
        
            
        questions = data.get(entity, [])
        print("questions:")
        print(questions)
        # if not questions:
        #     print(f"empty {entity} from json , print DATA:")
        #     print(data)
        #     print(f"data.get({entity}, [])")
        #     print(data.get('messages', []))

        # Check if the index exists
        # We use n-1 to convert 1-based human counting to 0-based indexing
        if n < 1 or n > len(questions):
            raise IndexError(f"Question number {n} is out of range. Total questions: {len(questions)}")
        
        # Return only the 'body' string
        return questions[n - 1]["body"]

    except FileNotFoundError:
        raise FileNotFoundError(f"The file at {json_path} was not found.")
    except json.JSONDecodeError:
        raise ValueError("The file is not a valid JSON format.")
    except KeyError:
        raise KeyError("The JSON structure is missing the 'questions' or 'body' keys.")

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
    return str(response)


@app.route("/handle-speech", methods=['POST', 'GET'])
def handle_speech():
    lang = request.args.get('lang') or session.get('lang') or 'he-IL'
    batch = request.args.get('batch') or session.get('batch') or 'basic'
    patientName = request.args.get('name') or session.get('name') or 'noname'
    current_questionId = int(request.args.get('questionId', 2)) or int(session.get('questionId')) or 2
    recording_url = request.form.get('RecordingUrl')
    recording_sid = request.form.get('RecordingSid')
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    print("############# handle_speech ##############")
    print(f"session lunguage = {lang}")
    print(f"RecordingUrl: {recording_url}.wav")
    print(f"RecordingSid: {recording_sid}")
    print(f"Twilio Account SID: {account_sid}")
    print(f"session questionId = {current_questionId}")

    """Endpoint that receives the recognized text from Twilio."""
    # Twilio sends the recognized text in the 'SpeechResult' parameter
    #speech_result = request.form.get('SpeechResult', '').lower()
    # speech_result = request.form.get('TranscriptionText', '').lower()
    patient_phone = request.form.get('To','') 
        
    # print("\n--- Form Data (Twilio sends data here) ---")
    # for key, value in request.form.items():
    #     print(f"{key}: {value}")

    # DEBUG: Print the type and the raw value
    print(f"patient phone: {patient_phone}")

    response = VoiceResponse()
    
    
    voice_model = read_question_from_json(lang, batch, VOICE_ALGO, "config")
    # you_said = read_question_from_json(lang, batch, YOU_SAID, "messages")
    # response.say(
    #     you_said.format(speech_result),
    #     language=f'{lang}',
    #     voice=voice_model #'Google.he-IL-Standard-A'
    # )

    next_questionId = current_questionId + 1
    try:
        read_question_from_json(lang, batch, next_questionId, "questions")
        # next_url = url_for('voice', questionId=next_questionId, lang=lang )
        next_url = f"/voice?lang={lang}&questionId={next_questionId}&name={quote(patientName)}&batch={batch}"
        print(f"new Url: {next_url}")
        response.redirect(next_url)
    except Exception as e:
        print(e)
        thanks = read_question_from_json(lang, batch, THANKS, "messages")
        response.say(
            thanks,
            language=f'{lang}', 
            voice=voice_model #'Google.he-IL-Standard-A'
        )
        response.hangup()
        
    # if 'סיום' in speech_result or 'סוף' in speech_result or 'end' in speech_result:
    #     response.say(
    #         "הבנתי שברצונך לסיים. שלום ויום טוב",
    #         language=f'{lang}',
    #         voice='Google.he-IL-Standard-A'
    #     )
    #     response.hangup() 
    #     return str(response)

    return str(response)

@app.route("/handle-recording-log", methods=['POST'])
# @csrf.exempt
def handle_recording_log():
    # 1. Get Params from the URL and Twilio Body
    print("########################### the germans got there!!!!!!!!!!!!!!!")
    call_sid = request.values.get('CallSid')
    recording_sid = request.values.get('RecordingSid')
    recording_url = request.values.get('RecordingUrl') # The link to the audio
    
    lang = request.args.get('lang')
    patient_name = request.args.get('name')
    question_id = request.args.get('questionId')

    # 2. Format the line for your file
    # We append '.wav' to the RecordingUrl for high-quality prosody audio
    log_entry = f"{call_sid}|{recording_sid}|{patient_name}|Q{question_id}|{recording_url}.wav\n"

    # 3. Append to your file
    file_path = Path(__file__).parent.resolve() / "prosody_tasks.txt"
    with open(file_path, "a", encoding='utf-8') as f:
        f.write(log_entry)

    return "", 200 # Twilio just needs a 200 OK

@app.route("/handle-transcription", methods=['GET','POST'])
def handle_transcription():
    # This is where the text arrives 5-10 seconds later
    call_sid = request.values.get('CallSid')
    speech_text = request.form.get('TranscriptionText')
    recording_sid = request.form.get('RecordingSid')
    
    print(f"BACKGROUND PROCESS: Text for call {call_sid}, recording {recording_sid} is: {speech_text}")
    
    # Update your database record where RecordingSid matches
    # survey_entry = Survey.query.filter_by(recording_sid=recording_sid).first()
    # survey_entry.text_answer = speech_text
    # db.session.commit()

    return '', 204 # Return empty success response to Twilio

import json

@app.route('/handle-realtime-text', methods=['POST'])
def handle_realtime_text():
    # patient_phone = request.form.get('To','') 
    # twillio_phone = request.form.get('From','') 
    patient_phone = request.values.get('to_phone') 
    twillio_phone = request.values.get('from_phone')
    print(f"from: {twillio_phone}, to: {patient_phone}")
    
    # Get the raw string data
    raw_data = request.form.get('TranscriptionData') # Verify the field name from Twilio
    print(f"THE DATA :::::::::::::: {raw_data}")
    try:
        # Convert the string to a dictionary
        transcription_data = json.loads(raw_data)
        speech_text = transcription_data.get('transcript')
        if speech_text:
            message_sid = send_survey_summary(twillio_phone, patient_phone, speech_text)
            print(f"============twilio: {twillio_phone}, patient: {patient_phone}, YOU SAY - TEXT {speech_text}")  

    except (json.JSONDecodeError, AttributeError, TypeError):
        # Fallback if it's already a dict or if it's empty
        speech_text = raw_data 

    # Rest of your logic...
    # add the text to the specific line in the call data file.


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


def survey_error(survey, send_function):
    if not survey:
        send_function('Sorry, but there are no surveys to be answered.')
        return True
    elif not survey.has_questions:
        send_function('Sorry, there are no questions for this survey.')
        return True
    return False

