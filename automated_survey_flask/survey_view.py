from . import app
from .models import Survey
from flask import url_for, session, request
from twilio.twiml.voice_response import VoiceResponse, Gather, Say
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import os

@app.route('/voice', methods=['GET', 'POST'])
def voice_survey():
    # response = VoiceResponse()

    # survey = Survey.query.first()
    # if survey_error(survey, response.say):
    #     return str(response)

    # welcome_user(survey, response.say)
    # redirect_to_first_question(response, survey)
    # return str(response)

    ##-------------------
    """TwiML endpoint that asks the first question using Speech Recognition."""
    response = VoiceResponse()
    
    # ✅ NEW CODE: GATHERING SPEECH
    gather = Gather(
        input='speech', # Set input type to speech
        speech_timeout='40',  #'auto', # Automatically ends listening when speech stops
        language='en-US',
        action='/handle-speech'
        #action="https://expectative-refugio-bizarrely.ngrok-free.dev/handle-speech" # New route to handle the text result

    )
    
    # Ask the first question
    gather.say("Welcome to the automated survey. How is your day going so far?")
    
    response.append(gather)
    response.say("Sorry, I didn't catch that. Goodbye!")
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
    """Endpoint that receives the recognized text from Twilio."""
    # Twilio sends the recognized text in the 'SpeechResult' parameter
    speech_result = request.form.get('SpeechResult', '').lower()
    
    response = VoiceResponse()
    response.say(f"you said {speech_result}")
    
    if 'end' in speech_result:
        response.say("Goodbye!")
        response.hangup() 

    response.say("Thank you! Starting the survey now. ")
    

    # if 'start' in speech_result:
    #     response.say("Thank you! Starting the survey now. How is your day going so far?")
    #     # TODO: Add logic here to redirect to your next question route
    # elif 'end' in speech_result:
    #     response.say("Goodbye!")
    #     response.hangup()    
    # else:
    #     response.say(f"I heard {speech_result}. That didn't match the options. Goodbye!")
    #     response.hangup()
        
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
