from twilio.rest import Client
import os
import argparse
import urllib.parse
from automated_survey_flask import app
from automated_survey_flask.models import db, Call

parser = argparse.ArgumentParser(description="eMolter LLM Survey Call Trigger")
parser.add_argument("--phone",  help="Target phone number (E.164 format)", required=True)
parser.add_argument("--lang",   help="Language locale, e.g. he-IL",        required=True)
parser.add_argument("--name",   help="Patient nickname",                    required=True)
parser.add_argument("--batch",  help="Question batch name",                 required=True)


def initiate_llm_survey_call():
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token  = os.environ.get("TWILIO_AUTH_TOKEN")
    args = parser.parse_args()

    if not all([account_sid, auth_token]):
        print("🚨 Error: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not set.")
        return

    client        = Client(account_sid, auth_token)
    TWILIO_NUMBER = "+17473023043"
    BASE_URL      = "https://www.emolter.org:5000"
    YOUR_NUMBER   = args.phone

    try:
        patient_name = urllib.parse.quote(args.name)
        call = client.calls.create(
            to=YOUR_NUMBER,
            from_=TWILIO_NUMBER,
            url=(
                f'{BASE_URL}/llm-voice?lang={args.lang}'
                f'&name={patient_name}'
                f'&batch={args.batch}'
                f'&to_phone={urllib.parse.quote(YOUR_NUMBER)}'
                f'&from_phone={urllib.parse.quote(TWILIO_NUMBER)}'
            ),
            record=True,
            recording_channels='dual',
            recording_status_callback=f'{BASE_URL}/llm-recording-callback',
            recording_status_callback_method='POST',
        )
        print(f"🎉 LLM survey call initiated to {YOUR_NUMBER}")
        print(f"Twilio SID: {call.sid}")

        with app.app_context():
            db.init_app(app)
            # question_id=0 is the sentinel for LLM-survey records
            call_record = Call(
                callSid=call.sid,
                recordSid=None,
                questionId=0,
                recordingUrl=None,
                conversationText='[]',
                patientPhone=YOUR_NUMBER,
                carrierPhone=TWILIO_NUMBER,
                questionsFile=f"questions_{args.batch}_{args.lang}.json",
            )
            db.session.add(call_record)
            db.session.commit()
            print("✅ Database record created (question_id=0, LLM survey).")

    except Exception as e:
        print(f"🛑 Call failed: {e}")


if __name__ == "__main__":
    initiate_llm_survey_call()
