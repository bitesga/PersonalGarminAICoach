#!/usr/bin/env python3
"""Test script for SMTP mail sending with web.de credentials."""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

def test_smtp_send() -> None:
    """Test sending a mail via SMTP."""
    print("=" * 60)
    print("SMTP Mail Test - PersonalGarminAICoach")
    print("=" * 60)
    print()

    # Load credentials from environment
    username = os.getenv("MAIL_USERNAME", "").strip()
    password = os.getenv("MAIL_PASSWORD", "").strip()

    print(f"Username: {username if username else '(not set)'}")
    print(f"Password: {'(set)' if password else '(not set)'}")
    print()

    if not username or not password:
        print("❌ FEHLER: MAIL_USERNAME oder MAIL_PASSWORD nicht in .env gesetzt.")
        return

    # SMTP server settings
    smtp_server = "smtp.web.de"
    smtp_port = 587
    recipient_email = "khalidlakniti@web.de"  # Send to self for testing
    subject = "PersonalGarminAICoach SMTP Test"
    body = "Dies ist eine Testmail aus dem PersonalGarminAICoach Workspace.\n\nWenn du diese Mail erhalten hast, funktioniert das SMTP-System!"

    # HTML Version
    body_html = """
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <div style="text-align: center; margin-bottom: 20px;">
            <img src="cid:fit_heart" alt="Fitness Heart Logo" style="width: 100px; height: auto;">
        </div>
        <h1 style="color: #38bdf8;">PersonalGarminAICoach</h1>
        <p>Dies ist eine <strong>Testmail</strong> aus dem PersonalGarminAICoach Workspace.</p>
        <p style="color: #16a34a; font-weight: bold;">✓ Wenn du diese Mail erhalten hast, funktioniert das SMTP-System!</p>
        <hr style="border: none; border-top: 1px solid #ccc; margin: 20px 0;">
        <p style="font-size: 12px; color: #666;">Fitness-Coach powered by Garmin + AI</p>
    </body>
    </html>
    """

    print(f"SMTP Server: {smtp_server}:{smtp_port}")
    print(f"Von: {username}")
    print(f"An: {recipient_email}")
    print(f"Betreff: {subject}")
    print()

    # Build message with HTML and attachment
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = recipient_email
    msg_alternative = MIMEMultipart("alternative")
    msg.attach(msg_alternative)
    msg_alternative.attach(MIMEText(body, "plain"))
    msg_alternative.attach(MIMEText(body_html, "html"))

    # Embed image
    image_path = Path(__file__).resolve().parent / "images" / "fit_heart.png"
    if image_path.exists():
        print(f"Bild eingebettet: {image_path.name}")
        with open(image_path, "rb") as attachment:
            image_part = MIMEBase("image", "png")
            image_part.set_payload(attachment.read())
        encoders.encode_base64(image_part)
        image_part.add_header(
            "Content-Disposition",
            "inline; filename=fit_heart.png",
        )
        image_part.add_header("Content-ID", "<fit_heart>")
        image_part.add_header("Content-Transfer-Encoding", "base64")
        msg.attach(image_part)
    else:
        print(f"⚠ Bild nicht gefunden: {image_path}")

    print("Verbinde mit SMTP-Server...")
    try:
        smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        print("✓ Verbindung hergestellt")

        print("Starte TLS...")
        smtp.starttls()
        print("✓ TLS gestartet")

        print("Authentifizierung...")
        smtp.login(username, password)
        print("✓ Authentifizierung erfolgreich")

        print("Sende Mail...")
        result = smtp.sendmail(username, [recipient_email], msg.as_string())
        print(f"✓ Mail gesendet (result: {result})")

        smtp.quit()
        print("✓ Verbindung beendet")
        print()
        print("=" * 60)
        print("✅ SMTP Test erfolgreich!")
        print("=" * 60)

    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Authentifizierungsfehler: {e}")
        print("   Prüfe MAIL_USERNAME und MAIL_PASSWORD in .env")
    except smtplib.SMTPException as e:
        print(f"❌ SMTP Fehler: {e}")
    except Exception as e:
        print(f"❌ Unerwarteter Fehler: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_smtp_send()
