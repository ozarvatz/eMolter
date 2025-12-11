from twilio.rest import Client
import os

# # 1. Credentials (Assumed to be set via environment variables)
# account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
# auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
# client = Client(account_sid, auth_token)

# # 2. ➡️ ADD YOUR NUMBERS HERE ⬅️
# TWILIO_NUMBER = "+17473023043" # ⬅️ Your purchased Twilio phone number
# YOUR_NUMBER = "+972503220778"  # ⬅️ The number you want to call

# # 3. Initiation Command
# call = client.calls.create(
#     to=YOUR_NUMBER,      # The number Twilio should call (your phone)
#     from_=TWILIO_NUMBER, # The number the call should come from (your Twilio number)
#     url='https://expectative-refugio-bizarrely.ngrok-free.dev/voice' # The URL Twilio hits for instructions
# )

# print(f"Call initiated. SID: {call.sid}")

# from twilio.rest import Client
# import os

def initiate_survey_call():
    """
    Initiates an outbound call to start the automated survey.
    """
    
    # 1. Twilio Credentials
    # These should be set as environment variables for security (e.g., in your shell: export TWILIO_ACCOUNT_SID='ACxxxxxxxx')
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    
    if not all([account_sid, auth_token]):
        print("🚨 Error: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not found in environment variables.")
        print("Please set them before running the script.")
        return

    client = Client(account_sid, auth_token)

    # 2. ⚠️ IMPORTANT: Fill in your actual numbers and the Ngrok URL here ⚠️
    # Numbers must be in E.164 format (e.g., +15551234567)
    TWILIO_NUMBER = "+17473023043" # ⬅️ Your purchased Twilio phone number
    YOUR_NUMBER = "+972503220778"  # ⬅️ The number you want to call
    NGROK_URL = "https://expectative-refugio-bizarrely.ngrok-free.dev" # ⬅️ Your live Ngrok URL
    
    # Check for placeholder values
    if TWILIO_NUMBER.startswith("+[") or YOUR_NUMBER.startswith("+[") or "NGROK_ID" in NGROK_URL:
        print("🛑 Error: Please update the TWILIO_NUMBER, YOUR_NUMBER, and NGROK_URL variables in the script.")
        return

    # 3. Initiation Command: The 'url' parameter is the endpoint Twilio will hit for TwiML instructions
    try:
        call = client.calls.create(
            to=YOUR_NUMBER,
            from_=TWILIO_NUMBER,
            url=f'{NGROK_URL}/voice' # Assumes your TwiML route is /twilio/voice
        )
        print(f"🎉 Call initiated successfully to {YOUR_NUMBER}.")
        print(f"Twilio SID: {call.sid}")
        print("Twilio will now connect to your Flask server's Ngrok URL.")
        
    except Exception as e:
        print(f"🛑 Call failed: {e}")
        print("Check your Twilio credentials, number formats, and Twilio trial settings.")


if __name__ == "__main__":
    initiate_survey_call()
