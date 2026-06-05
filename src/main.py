from pathlib import Path
import functools, builtins
from datetime import datetime

from config import Settings, validate
from scrape_key_metrics import scrape_key_metrics
from send_email import send_email
from send_whatsapp import send_whatsapp_text

print = functools.partial(builtins.print, flush=True)


def main() -> None:
    print("Starting Syrve report automation...")
    settings = Settings()
    validate(settings)
    print("Settings validated. Opening Syrve and scraping data...")

    output_dir = Path("output")
    report_file, html, plain_text = scrape_key_metrics(settings, output_dir)
    print("Scraping finished. Preparing delivery...")

    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"تقرير Key Metrics - اليوم السابق - {today}"

    # Save plain-text report to artifact as well.
    (output_dir / "whatsapp_report.txt").write_text(plain_text, encoding="utf-8")

    whatsapp_error = None
    whatsapp_sent = False

    if settings.whatsapp_enabled:
        try:
            # Your WhatsApp API sends text messages. We split long reports into chunks.
            max_len = 3000
            chunks = [plain_text[i:i + max_len] for i in range(0, len(plain_text), max_len)] or [plain_text]
            for idx, chunk in enumerate(chunks, start=1):
                prefix = f"{subject}\nالجزء {idx}/{len(chunks)}\n\n" if len(chunks) > 1 else f"{subject}\n\n"
                print(f"Sending WhatsApp chunk {idx}/{len(chunks)}...")
                send_whatsapp_text(settings, prefix + chunk)
            whatsapp_sent = True
        except Exception as exc:  # noqa: BLE001
            whatsapp_error = exc
            print(f"WhatsApp delivery failed: {exc}")

    email_sent = False

    if settings.email_enabled:
        print("Sending email report because EMAIL_ENABLED=true...")
        send_email(settings, subject, html, report_file)
        email_sent = True

    if whatsapp_error and settings.email_fallback_enabled and not email_sent:
        fallback_subject = f"[WhatsApp failed] {subject}"
        fallback_html = html + f"<p><b>ملاحظة:</b> فشل إرسال واتساب، لذلك تم إرسال التقرير على الإيميل. الخطأ: {whatsapp_error}</p>"
        print("Sending fallback email because WhatsApp failed...")
        send_email(settings, fallback_subject, fallback_html, report_file)
        email_sent = True

    if whatsapp_error and not email_sent:
        raise whatsapp_error

    print(f"Report automation completed. whatsapp_sent={whatsapp_sent}, email_sent={email_sent}")


if __name__ == "__main__":
    main()
