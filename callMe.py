from twilio.rest import Client
import os
import argparse
import  urllib.parse

parser = argparse.ArgumentParser(description="eMolter Call Traigger")
parser.add_argument("--phone", help = "The target phone number", required = True)
parser.add_argument("--lang", help = "The language to talk", required = True)
parser.add_argument("--name", help = "The patient nik name", required = True)

def initiate_survey_call():
    """
    Initiates an outbound call to start the automated survey.
    """
    
    # 1. Twilio Credentials
    # These should be set as environment variables for security (e.g., in your shell: export TWILIO_ACCOUNT_SID='ACxxxxxxxx')
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    args = parser.parse_args()
    
    if not all([account_sid, auth_token]):
        print("🚨 Error: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not found in environment variables.")
        print("Please set them before running the script.")
        return

    client = Client(account_sid, auth_token)

    # 2. ⚠️ IMPORTANT: Fill in your actual numbers and the Ngrok URL here ⚠️
    # Numbers must be in E.164 format (e.g., +15551234567)
    TWILIO_NUMBER = "+17473023043" # ⬅️ Your purchased Twilio phone number
    YOUR_NUMBER = args.phone
    # YOUR_NUMBER = "+972503220778"  #OZ ⬅️ The number you want to call
    # YOUR_NUMBER = "+972546646637"  #Seev ⬅️ The number you want to call
    NGROK_URL = "https://expectative-refugio-bizarrely.ngrok-free.dev" # ⬅️ Your live Ngrok URL
    
    # Check for placeholder values
    if TWILIO_NUMBER.startswith("+[") or YOUR_NUMBER.startswith("+[") or "NGROK_ID" in NGROK_URL:
        print("🛑 Error: Please update the TWILIO_NUMBER, YOUR_NUMBER, and NGROK_URL variables in the script.")
        return

    # 3. Initiation Command: The 'url' parameter is the endpoint Twilio will hit for TwiML instructions
    try:
        patientName = urllib.parse.quote(args.name)
        call = client.calls.create(
            to=YOUR_NUMBER,
            from_=TWILIO_NUMBER,
            url=f'{NGROK_URL}/voice?lang={args.lang}&name={patientName}', # Assumes your TwiML route is /voice
            record=True,
            recording_channels='dual'
        )
        print(f"🎉 Call initiated successfully to {YOUR_NUMBER}.")
        print(f"Twilio SID: {call.sid}")
        print("Twilio will now connect to your Flask server's Ngrok URL.")
        
    except Exception as e:
        print(f"🛑 Call failed: {e}")
        print("Check your Twilio credentials, number formats, and Twilio trial settings.")


if __name__ == "__main__":
    initiate_survey_call()
