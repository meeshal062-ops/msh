import requests
from config import Settings


def send_whatsapp_text(settings: Settings, text: str) -> None:
    """Send a WhatsApp text message using the custom Wats API.

    Expected secrets/env:
      WATS_API_URL=https://wats-enzn.onrender.com/api/v1/send
      WATS_API_TOKEN=your_token
      WATS_TO=9665xxxxxxxx
    """
    if not settings.whatsapp_enabled:
        print("WhatsApp sending is disabled.")
        return

    if not (settings.wats_api_url and settings.wats_api_token and settings.wats_to):
        raise RuntimeError("WhatsApp is enabled but WATS_API_URL/WATS_API_TOKEN/WATS_TO is missing.")

    headers = {
        "Authorization": f"Bearer {settings.wats_api_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": settings.wats_to,
        "message": text,
    }
    response = requests.post(settings.wats_api_url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    print(f"WhatsApp report sent to {settings.wats_to}")
