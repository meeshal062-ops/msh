from pathlib import Path
import functools, builtins
from datetime import datetime

from config import Settings, validate
from scrape_sales_by_product import scrape_sales_by_product
from send_email import send_email
from send_whatsapp import send_whatsapp_text, send_whatsapp_media
from pdf_report import build_pdf_report

print = functools.partial(builtins.print, flush=True)


def main() -> None:
    print("Starting Syrve report automation...")
    settings = Settings()
    validate(settings)
    print("Settings validated. Opening Syrve and scraping data...")

    output_dir = Path("output")
    report_file, html, plain_text = scrape_sales_by_product(settings, output_dir)
    print("Scraping finished. Preparing delivery...")

    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"Sales by Product Report - Previous Business Day - {today}"

    # Save plain-text report and PDF dashboard to artifacts.
    (output_dir / "whatsapp_report.txt").write_text(plain_text, encoding="utf-8")
    pdf_file = build_pdf_report(html, output_dir, "sales_dashboard.pdf")
    print(f"PDF dashboard generated: {pdf_file}")

    whatsapp_error = None
    whatsapp_sent = False

    if settings.whatsapp_enabled:
        try:
            message = f"{subject}\n\nAttached: PDF Sales Dashboard."
            print("Sending WhatsApp PDF dashboard...")
            send_whatsapp_media(settings, message, pdf_file)
            whatsapp_sent = True
        except Exception as exc:  # noqa: BLE001
            whatsapp_error = exc
            print(f"WhatsApp PDF delivery failed: {exc}")
            # Secondary fallback: text through WhatsApp.
            try:
                print("Trying WhatsApp text fallback...")
                send_whatsapp_text(settings, f"{subject}\n\n" + plain_text[:3000])
                whatsapp_sent = True
                whatsapp_error = None
            except Exception as text_exc:  # noqa: BLE001
                whatsapp_error = text_exc
                print(f"WhatsApp text fallback also failed: {text_exc}")

    email_sent = False

    if settings.email_enabled:
        print("Sending email report because EMAIL_ENABLED=true...")
        send_email(settings, subject, html, pdf_file)
        email_sent = True

    if whatsapp_error and settings.email_fallback_enabled and not email_sent:
        fallback_subject = f"[WhatsApp failed] {subject}"
        fallback_html = html + f"<p><b>Note:</b> WhatsApp delivery failed, so this report was sent by email. Error: {whatsapp_error}</p>"
        print("Sending fallback email because WhatsApp failed...")
        send_email(settings, fallback_subject, fallback_html, pdf_file)
        email_sent = True

    if whatsapp_error and not email_sent:
        raise whatsapp_error

    print(f"Report automation completed. whatsapp_sent={whatsapp_sent}, email_sent={email_sent}")


if __name__ == "__main__":
    main()
