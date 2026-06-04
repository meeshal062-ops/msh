from pathlib import Path
import smtplib
from email.message import EmailMessage
from config import Settings


def send_email(settings: Settings, subject: str, html_body: str, attachment: Path) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = settings.email_to
    msg.set_content("Daily sales report is attached.")
    msg.add_alternative(html_body, subtype="html")

    data = attachment.read_bytes()
    msg.add_attachment(
        data,
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=attachment.name,
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)

    print(f"Email sent to {settings.email_to}")
