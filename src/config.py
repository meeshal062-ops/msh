from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: str = "false") -> bool:
    return _get(name, default).lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    # Syrve
    syrve_url: str = _get("SYRVE_URL", "https://half-million-co.syrve.app/dashboard/index.html#/dashboard/174327")
    syrve_username: str = _get("SYRVE_USERNAME")
    syrve_password: str = _get("SYRVE_PASSWORD")
    syrve_report_url: str = _get("SYRVE_REPORT_URL")
    syrve_main_menu_text: str = _get("SYRVE_MAIN_MENU_TEXT", "Routine Restaurant Ope")
    syrve_reports_section_text: str = _get("SYRVE_REPORTS_SECTION_TEXT", "التقارير")
    syrve_report_name: str = _get("SYRVE_REPORT_NAME", "Key Metrics")
    branch_codes: str = _get("BRANCH_CODES", "B60,B63,B45,B26,B105")
    report_date_mode: str = _get("REPORT_DATE_MODE", "yesterday")  # today or yesterday
    sales_target: float = float(_get("SALES_TARGET", "80500") or "80500")

    # Email is optional.
    # EMAIL_ENABLED=true sends email every successful run.
    # EMAIL_FALLBACK_ENABLED=true sends email only if WhatsApp delivery fails.
    email_enabled: bool = _bool("EMAIL_ENABLED", "false")
    email_fallback_enabled: bool = _bool("EMAIL_FALLBACK_ENABLED", "false")
    smtp_host: str = _get("SMTP_HOST")
    smtp_port: int = int(_get("SMTP_PORT", "587") or "587")
    smtp_username: str = _get("SMTP_USERNAME")
    smtp_password: str = _get("SMTP_PASSWORD")
    email_from: str = _get("EMAIL_FROM")
    email_to: str = _get("EMAIL_TO")

    # WhatsApp via your custom Wats API
    whatsapp_enabled: bool = _bool("WHATSAPP_ENABLED", "true")
    wats_api_url: str = _get("WATS_API_URL", "https://wats-enzn.onrender.com/api/v1/send")
    wats_api_token: str = _get("WATS_API_TOKEN")
    wats_to: str = _get("WATS_TO")


def validate(settings: Settings) -> None:
    missing = []

    for key in ["syrve_username", "syrve_password"]:
        if not getattr(settings, key):
            missing.append(key.upper())

    if settings.whatsapp_enabled:
        for key in ["wats_api_url", "wats_api_token", "wats_to"]:
            if not getattr(settings, key):
                missing.append(key.upper())

    if settings.email_enabled or settings.email_fallback_enabled:
        for key in ["smtp_host", "smtp_username", "smtp_password", "email_from", "email_to"]:
            if not getattr(settings, key):
                missing.append(key.upper())

    if missing:
        raise RuntimeError("Missing required settings/secrets: " + ", ".join(missing))
