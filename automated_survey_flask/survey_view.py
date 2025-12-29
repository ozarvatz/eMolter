from . import app
from .models import Survey
from flask import url_for, session, request
from twilio.twiml.voice_response import VoiceResponse, Gather, Say
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import os

@app.route('/voice', methods=['GET', 'POST'])
def voice_survey():
    lang = request.args.get('lang')
    if lang:
        session['lang'] = lang
    print(f"param lunguage = {lang}")
    """TwiML endpoint that asks the first question using Speech Recognition."""
    response = VoiceResponse()
    
    # ✅ NEW CODE: GATHERING SPEECH
    gather = Gather(
        input='speech', # Set input type to speech
        speech_timeout='6', #'auto', # Automatically ends listening when speech stops
        language=f'{lang}', #'iw-IL', #'en-US',
        # voice='Google.he-IL-Standard-A',
        # hints='שלום, להתראות, סיום, הכל טוב, גרוע, בסדר, מצוין', # Add Hebrew hints here
        # speech_model='telephony', #'experimental_conversations', # Try this model
        # enhanced=True,
        action='/handle-speech',
        method='POST'
        # action="https://expectative-refugio-bizarrely.ngrok-free.dev/handle-speech" # New route to handle the text result

    )
    
    
    # Ask the first question
    # gather.say("Welcome to the automated survey. How is your day going so far?")
    gather.say(
        "הי זה אני 'אימולטר', איך היום שלך עד כו?", 
        language=f'{lang}', #'iw-IL'
        voice='Google.he-IL-Standard-A',
        # hints='שלום, להתראות, סיום, הכל טוב, גרוע, בסדר, מצוין', # Add Hebrew hints here
        # speech_model='experimental_conversations' # Try this model
    )
    
    response.append(gather)
    response.say(
        "מצטער, התשובה לא נקלטה, שלום ותודה!!",
        language=f'{lang}', #'iw-IL'
        voice='Google.he-IL-Standard-A'   
    )
    response.hangup()
    
    return str(response)


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
    lang = session['lang']
    print(f"session lunguage = {lang}")
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
    
    response.say(
        f":אתה אמרת '{speech_result}'", 
        language=f'{lang}', #'iw-IL'
        voice='Google.he-IL-Standard-A'
    )

    if 'סיום' in speech_result or 'סוף' in speech_result or 'end' in speech_result:
        response.say(
            "הבנתי שברצונך לסיים. שלום ויום טוב",
            language=f'{lang}', #'iw-IL'
            voice='Google.he-IL-Standard-A'
        )
        response.hangup() 
        return str(response)

    
    response.say(
        "תודה על התשובה. בהמשך יהיו עוד שאלות, שלום והמשך יום טוב!",
        llanguage=f'{lang}', #'iw-IL'
        voice='Google.he-IL-Standard-A'
    )
    
        
    return str(response)

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
