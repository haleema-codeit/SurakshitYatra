import os
from fast_sms import send_fast_sms
from telegram_notify import send_telegram_notify

def test_everything(test_phone_number):
    print("="*50)
    print("🚀 TESTING PUSHBULLET (Real SMS via your Android Phone)")
    print("="*50)
    
    sms_body = "TEST: This is a test SMS from Surakshit Yatra!"
    print(f"Sending SMS to {test_phone_number}...")
    
    pb_result = send_fast_sms(test_phone_number, sms_body)
    
    if pb_result.get("return"):
        print(f"✅ SUCCESS! SMS sent via your Android phone.")
    else:
        print(f"❌ FAILED. Pushbullet error: {pb_result.get('message') or pb_result}")


    print("\n" + "="*50)
    print("🚀 TESTING TELEGRAM")
    print("="*50)
    
    telegram_body = f"TEST: This is a test Telegram notification for {test_phone_number} from Surakshit Yatra!"
    print("Sending Telegram message...")
    
    telegram_result = send_telegram_notify(telegram_body)
    
    if telegram_result.get("ok"):
        print("✅ SUCCESS! Check your Telegram app.")
    else:
        print(f"❌ FAILED. Telegram error: {telegram_result.get('error') or telegram_result}")

if __name__ == "__main__":
    # Put your actual phone number here in E.164 format (e.g. +919876543210)
    YOUR_PHONE_NUMBER = "+919500874268" # <-- Change this to your real receiving mobile number
    
    test_everything(YOUR_PHONE_NUMBER)
