import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("PUSHBULLET_API_KEY")

def send_fast_sms(to: str, message: str) -> dict:
    """
    Sends a real cellular SMS via Pushbullet (uses your Android phone's SIM card).
    Free tier: 100 SMS/month. No internet needed on the recipient's side.

    Parameters
    ----------
    to : str
        Destination phone number in E.164 format, e.g. +919500874268
    message : str
        The SMS body text
    """
    if not API_KEY:
        return {"return": False, "message": "Missing PUSHBULLET_API_KEY in .env"}

    # Step 1: Get the list of devices to find your Android phone
    headers = {
        "Access-Token": API_KEY,
        "Content-Type": "application/json"
    }

    try:
        # Find your Android phone's device identifier
        devices_resp = requests.get(
            "https://api.pushbullet.com/v2/devices",
            headers=headers,
            timeout=10
        )
        devices_resp.raise_for_status()
        devices = devices_resp.json().get("devices", [])

        # Pick the first active Android device
        phone_iden = None
        for device in devices:
            if device.get("active") and device.get("has_sms"):
                phone_iden = device.get("iden")
                break

        if not phone_iden:
            return {"return": False, "message": "No SMS-capable Android device found on your Pushbullet account. Make sure the Pushbullet app is installed and SMS sync is enabled."}

        # Step 2: Create an SMS thread (send the text)
        # First get the user identity
        me_resp = requests.get(
            "https://api.pushbullet.com/v2/users/me",
            headers=headers,
            timeout=10
        )
        me_resp.raise_for_status()
        user_iden = me_resp.json().get("iden")

        # Send the SMS via Pushbullet's texting API
        sms_payload = {
            "data": {
                "addresses": [to],
                "guid": f"surakshit-{to}-{os.urandom(4).hex()}",
                "message": message,
                "target_device_iden": phone_iden
            }
        }

        sms_resp = requests.post(
            "https://api.pushbullet.com/v2/texts",
            headers=headers,
            json=sms_payload,
            timeout=15
        )
        sms_resp.raise_for_status()
        result = sms_resp.json()

        return {"return": True, "message": "SMS sent via your Android phone!", "data": result}

    except requests.exceptions.HTTPError as e:
        # Try to get the actual error message from Pushbullet
        try:
            error_detail = e.response.json().get("error", {}).get("message", str(e))
        except Exception:
            error_detail = str(e)
        return {"return": False, "message": error_detail}
    except Exception as e:
        return {"return": False, "message": str(e)}
