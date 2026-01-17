from . import app
from .models import Survey
from flask import url_for, session, request
from twilio.twiml.voice_response import VoiceResponse, Gather, Say
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import os
from pathlib import Path
import json
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
BASE_URL        = "https://expectative-refugio-bizarrely.ngrok-free.dev"
"""
Recommended Voices for a Strong German Accent:
Voice Engine	Gender	Style	TwiML Name
Google Wavenet	Male	Professional/Deep	Google.de-DE-Wavenet-B
Amazon Polly	Female	Natural/Clear	Polly.Vicki-Neural
Google Standard	Female	Clean/Direct	Google.de-DE-Standard-F
"""
@app.route('/voice', methods=['GET', 'POST'])
def voice_survey():
    lang = request.args.get('lang') or session.get('lang') or 'iw-IL'
    patientName = request.args.get('name') or session.get('name') or 'noname'
    batch = request.args.get('batch') or session.get('batch') or 'basic'
    current_questionId = int(request.args.get('questionId', 1)) or int(session.get('questionId')) or 1
    call_sid = request.values.get('CallSid')
   
    print(f"param lunguage = {lang}")
    print(f"param patient name = {patientName}")
    print(f"param batch = {batch}")
    print(f"param current questionID = {current_questionId}")
    """TwiML endpoint that asks the first question using Speech Recognition."""
    response = VoiceResponse()
    # response.record(
    #     #recording_status_callback=f'{BASE_URL}/handle-recording-log?lang={lang}&name=noname&batch={batch}&questionId={current_questionId}',
    #     recording_channels='dual', # Crucial for Prosody
    #     recording_status_callback=f'{BASE_URL}/handle-recording-log?name={patientName}',
    #     # action='/ignore-this-path' # Record needs an action or it might loop
    # )
    # ✅ NEW CODE: GATHERING SPEECH
    gather = Gather(
        input='speech', # Set input type to speech
        speech_timeout='2', #'auto', # Automatically ends listening when speech stops
        language=f'{lang}', #'iw-IL', #'en-US',
        # voice='Google.he-IL-Standard-A',
        # hints='שלום, להתראות, סיום, הכל טוב, גרוע, בסדר, מצוין', # Add Hebrew hints here
        # speech_model='telephony', #'experimental_conversations', # Try this model
        # enhanced=True,
        barge_in=False,
        
        record='record-from-answer-dual', 
        recording_status_callback=f'{BASE_URL}/handle-recording-log?lang={lang}&name={patientName}&batch={batch}&questionId={current_questionId}',
        
        action=f'/handle-speech?lang={lang}&name={patientName}&batch={batch}&questionId={current_questionId}',
        method='POST'
        # action="https://expectative-refugio-bizarrely.ngrok-free.dev/handle-speech" # New route to handle the text result

    )
    
    
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
            gather.say(
                hello_text_msg,
                language=f'{lang}', #'iw-IL'
                voice=voice_model #'Google.he-IL-Standard-A'
            )
        print(f"first question: {current_question_txt}")
    except Exception as e:
        print(f"error in read file {e}")
    
    

    gather.say(
        current_question_txt, 
        language=f'{lang}', #'iw-IL'
        voice=voice_model #'Google.he-IL-Standard-A'
        # hints='שלום, להתראות, סיום, הכל טוב, גרוע, בסדר, מצוין', # Add Hebrew hints here
        # speech_model='experimental_conversations' # Try this model
    )
    
    sorry_failed = read_question_from_json(lang, batch, SORRY_FAILED, "messages")
    response.append(gather)
    response.say(
        sorry_failed,
        language=f'{lang}', #'iw-IL'
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


@app.route("/handle-speech", methods=['POST'])
def handle_speech():
    lang = request.args.get('lang') or session.get('lang') or 'iw-IL'
    batch = request.args.get('batch') or session.get('batch') or 'basic'
    patientName = request.args.get('name') or session.get('name') or 'noname'
    current_questionId = int(request.args.get('questionId', 2)) or int(session.get('questionId')) or 2
    print("############# handle_speech ##############")
    print(f"session lunguage = {lang}")
    print(f"session questionId = {current_questionId}")
    print(f"session questionId type = {type(current_questionId)}")

    """Endpoint that receives the recognized text from Twilio."""
    # Twilio sends the recognized text in the 'SpeechResult' parameter
    speech_result = request.form.get('SpeechResult', '').lower()
    patient_phone = request.form.get('To','') 
        
    # print("\n--- Form Data (Twilio sends data here) ---")
    # for key, value in request.form.items():
    #     print(f"{key}: {value}")

    # DEBUG: Print the type and the raw value
    print(f"speech result: {speech_result}")
    print(f"patient phone: {patient_phone}")
    print(f"Type: {type(speech_result)}")

    response = VoiceResponse()
    
    you_said = read_question_from_json(lang, batch, YOU_SAID, "messages")
    voice_model = read_question_from_json(lang, batch, VOICE_ALGO, "config")
    response.say(
        you_said.format(speech_result),
        language=f'{lang}', #'iw-IL'
        voice=voice_model #'Google.he-IL-Standard-A'
    )

    next_questionId = current_questionId + 1
    try:
        read_question_from_json(lang, batch, next_questionId, "questions")
        # next_url = url_for('voice', questionId=next_questionId, lang=lang )
        next_url = f"/voice?lang={lang}&questionId={next_questionId}&name={patientName}&batch={batch}"
        print(f"new Url: {next_url}")
        response.redirect(next_url)
    except Exception as e:
        print(e)
        thanks = read_question_from_json(lang, batch, THANKS, "messages")
        response.say(
            thanks,
            language=f'{lang}', #'iw-IL'
            voice=voice_model #'Google.he-IL-Standard-A'
        )
        response.hangup()
        
    # if 'סיום' in speech_result or 'סוף' in speech_result or 'end' in speech_result:
    #     response.say(
    #         "הבנתי שברצונך לסיים. שלום ויום טוב",
    #         language=f'{lang}', #'iw-IL'
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

