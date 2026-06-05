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

    if settings.whatsapp_enabled:
        # Your WhatsApp API sends text messages. We split long reports into chunks.
        max_len = 3000
        chunks = [plain_text[i:i + max_len] for i in range(0, len(plain_text), max_len)] or [plain_text]
        for idx, chunk in enumerate(chunks, start=1):
            prefix = f"{subject}\nالجزء {idx}/{len(chunks)}\n\n" if len(chunks) > 1 else f"{subject}\n\n"
            print(f"Sending WhatsApp chunk {idx}/{len(chunks)}...")
            send_whatsapp_text(settings, prefix + chunk)

    if settings.email_enabled:
        print("Sending email report...")
        send_email(settings, subject, html, report_file)

    print("Report automation completed.")


if __name__ == "__main__":
    main()
