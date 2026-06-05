from pathlib import Path
import requests
from config import Settings


def send_whatsapp_text(settings: Settings, text: str) -> None:
    """Send a WhatsApp text message using the custom Wats API."""
    if not settings.whatsapp_enabled:
        print("WhatsApp sending is disabled.")
        return

    if not (settings.wats_api_url and settings.wats_api_token and settings.wats_to):
        raise RuntimeError("WhatsApp is enabled but WATS_API_URL/WATS_API_TOKEN/WATS_TO is missing.")

    headers = {
        "Authorization": f"Bearer {settings.wats_api_token}",
        "Content-Type": "application/json",
    }
    payload = {"to": settings.wats_to, "message": text}
    response = requests.post(settings.wats_api_url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    print(f"WhatsApp text sent to {settings.wats_to}")


def send_whatsapp_media(settings: Settings, message: str, media_path: Path) -> None:
    """Send a WhatsApp media/file message using the custom Wats API form-data endpoint."""
    if not settings.whatsapp_enabled:
        print("WhatsApp sending is disabled.")
        return

    if not (settings.wats_api_url and settings.wats_api_token and settings.wats_to):
        raise RuntimeError("WhatsApp is enabled but WATS_API_URL/WATS_API_TOKEN/WATS_TO is missing.")

    headers = {"Authorization": f"Bearer {settings.wats_api_token}"}
    with media_path.open("rb") as f:
        files = {"media": (media_path.name, f, "application/pdf")}
        data = {"to": settings.wats_to, "message": message}
        response = requests.post(settings.wats_api_url, headers=headers, data=data, files=files, timeout=120)
    response.raise_for_status()
    print(f"WhatsApp PDF sent to {settings.wats_to}: {media_path}")
