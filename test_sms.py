import os
from dotenv import load_dotenv
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

# Load environment variables from .env
load_dotenv()

def test_twilio_sms():
    print("--- 🛡️ Surakshit Yatra: Twilio Testing Utility ---")
    
    account_sid = os.getenv('TWILIO_ACCOUNT_SID')
    auth_token = os.getenv('TWILIO_AUTH_TOKEN')
    from_number = os.getenv('TWILIO_FROM_NUMBER')
    
    print(f"SID Loaded: {'✅' if account_sid and 'your_' not in account_sid else '❌ MISSING or DEFAULT'}")
    print(f"Token Loaded: {'✅' if auth_token and 'your_' not in auth_token else '❌ MISSING or DEFAULT'}")
    print(f"From Number: {from_number if from_number else '❌ MISSING'}")

    if not all([account_sid, auth_token, from_number]) or "your_" in account_sid:
        print("\n[!] ERROR: Twilio credentials are not set in .env")
        print("Please update your .env file with real credentials from the Twilio Console.")
        return

    to_number = input("\nEnter your mobile number to receive a test SMS (e.g. +919988776655): ")
    
    try:
        client = Client(account_sid, auth_token)
        print("\n[...] Sending test SMS via Twilio...")
        
        message = client.messages.create(
            body="🔴 Surakshit Yatra: Real SMS Test successful! Your emergency alert system is now active.",
            from_=from_number,
            to=to_number
        )
        
        print(f"\n✅ SUCCESS! Message sent.")
        print(f"Message SID: {message.sid}")
        print(f"Status: {message.status}")
        print("\nCheck your phone! If you received the message, the system is ready.")
        
    except TwilioRestException as e:
        print(f"\n❌ TWILIO REST ERROR: {e.msg}")
        print(f"Check your credentials and that the 'From Number' is correct.")
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {str(e)}")

if __name__ == "__main__":
    test_twilio_sms()
