"""Notification utilities for coach recommendations."""

from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _build_message(recommendation: dict[str, Any]) -> str:
    title = str(recommendation.get("titel", "Coach Empfehlung"))
    intensity = recommendation.get("intensitaet", "n/a")
    recommendation_text = str(recommendation.get("empfehlung", ""))
    reasoning = str(recommendation.get("begruendung", ""))
    latest_day = recommendation.get("latest_day", {}) if isinstance(recommendation.get("latest_day", {}), dict) else {}
    sleep_score = latest_day.get("sleep_score", "n/a")
    body_battery = latest_day.get("body_battery", "n/a")
    stress = latest_day.get("stress", "n/a")
    vo2_max = latest_day.get("vo2_max", "n/a")
    resting_hr = latest_day.get("resting_heart_rate", "n/a")

    if "Alternative:" in recommendation_text:
        main_recommendation, alternative_recommendation = recommendation_text.split("Alternative:", 1)
        main_recommendation = main_recommendation.strip().rstrip(".")
        alternative_recommendation = alternative_recommendation.strip().rstrip(".")
    else:
        main_recommendation = recommendation_text.strip()
        alternative_recommendation = "Keine Alternative vorhanden."

    body = (
        f"GUTEN MORGEN SPORTSFREUND!\n\n"
        f"DEINE TAGESWERTE:\n"
        f"SLEEP SCORE: {sleep_score}/100\n"
        f"BODY BATTERY: {body_battery}/100\n"
        f"STRESS: {stress}\n"
        f"VO2MAX: {vo2_max}\n"
        f"RHR: {resting_hr}\n\n"
        f"HAUPTEMPFEHLUNG: {title}\n{main_recommendation}\n\n"
        f"ALTERNATIVE:\n{alternative_recommendation}\n\n"
        f"INTENSITAET: {intensity}/10\n\n"
        f"BEGRUENDUNG:\n{reasoning}\n"
    )
    return body


def _resolve_discord_recipient(profile: dict[str, Any]) -> str:
    return str(
        profile.get("discord_user_id")
        or profile.get("linked_discord_id")
        or ""
    ).strip()


def _resolve_email_recipient(profile: dict[str, Any]) -> str:
    return str(
        profile.get("email")
        or profile.get("linked_email")
        or ""
    ).strip()


def _discord_api_post(url: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "PersonalGarminAICoach/1.0 (+https://discord.com/developers/docs)",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read().decode("utf-8")
        return json.loads(content) if content else {}


def send_discord_dm(message: str, user_id: str) -> tuple[bool, str]:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        return False, "DISCORD_BOT_TOKEN fehlt."
    if not user_id:
        return False, "Discord User-ID fehlt."

    try:
        channel_resp = _discord_api_post(
            "https://discord.com/api/v10/users/@me/channels",
            {"recipient_id": user_id},
            token,
        )
        channel_id = str(channel_resp.get("id", "")).strip()
        if not channel_id:
            return False, "Discord DM-Channel konnte nicht erstellt werden."

        _discord_api_post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            {"content": message[:1900]},
            token,
        )
        return True, "Discord-DM gesendet."
    except urllib.error.HTTPError as exc:
        try:
            error_payload = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            error_payload = ""
        return False, f"Discord API Fehler {exc.code}: {error_payload or exc.reason}"
    except Exception as exc:
        return False, f"Discord-Versand fehlgeschlagen: {exc}"


def send_verification_dm(user_id: str, code: str, invite_link: str | None = None) -> tuple[bool, str]:
    """Send a short verification DM containing the code and optional server invite instructions."""
    if not user_id:
        return False, "Discord User-ID fehlt."
    try:
        message = (
            f"Dein Verifizierungs-Code: {code}\n\n"
            "Gib diesen Code in der App ein, um deinen Account zu verifizieren."
            "Hinweis: Der Bot sendet dir nur diesen Code und eine kurze Anleitung."
        )
        return send_discord_dm(message, user_id)
    except Exception as exc:
        return False, f"Fehler beim Senden des Verifizierungs-Codes: {exc}"


def _build_message_html(recommendation: dict[str, Any]) -> str:
    """Build an HTML-formatted recommendation message."""
    title = str(recommendation.get("titel", "Coach Empfehlung"))
    intensity = recommendation.get("intensitaet", "n/a")
    recommendation_text = str(recommendation.get("empfehlung", ""))
    reasoning = str(recommendation.get("begruendung", ""))
    latest_day = recommendation.get("latest_day", {}) if isinstance(recommendation.get("latest_day", {}), dict) else {}
    sleep_score = latest_day.get("sleep_score", "n/a")
    body_battery = latest_day.get("body_battery", "n/a")
    stress = latest_day.get("stress", "n/a")
    vo2_max = latest_day.get("vo2_max", "n/a")
    resting_hr = latest_day.get("resting_heart_rate", "n/a")

    if "Alternative:" in recommendation_text:
        main_recommendation, alternative_recommendation = recommendation_text.split("Alternative:", 1)
        main_recommendation = main_recommendation.strip().rstrip(".")
        alternative_recommendation = alternative_recommendation.strip().rstrip(".")
    else:
        main_recommendation = recommendation_text.strip()
        alternative_recommendation = "Keine Alternative vorhanden."

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <div style="text-align: center; margin-bottom: 30px;">
            <img src="cid:fit_heart" alt="Fitness Heart Logo" style="width: 120px; height: auto;">
        </div>
        <h1 style="color: #38bdf8; text-align: center;">PersonalGarminAICoach</h1>
        
        <div style="background-color: #f0f9ff; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h2 style="color: #38bdf8; margin-top: 0;">Guten Morgen, Sportsfreund! 🏃</h2>
            <p style="margin: 10px 0;"><strong>Deine heutigen Tageswerte:</strong></p>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc;"><strong>Sleep Score:</strong></td>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc; color: #16a34a; font-weight: bold;">{sleep_score}/100</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc;"><strong>Body Battery:</strong></td>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc; color: #16a34a; font-weight: bold;">{body_battery}/100</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc;"><strong>Stress:</strong></td>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc; color: #16a34a; font-weight: bold;">{stress}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc;"><strong>VO2Max:</strong></td>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc; color: #16a34a; font-weight: bold;">{vo2_max}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Resting HR:</strong></td>
                    <td style="padding: 8px; color: #16a34a; font-weight: bold;">{resting_hr}</td>
                </tr>
            </table>
        </div>

        <div style="background-color: #f5f3ff; padding: 20px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid #a78bfa;">
            <h2 style="color: #7c3aed; margin-top: 0;">📋 Hauptempfehlung: {title}</h2>
            <p>{main_recommendation}</p>
        </div>

        <div style="background-color: #fef3c7; padding: 20px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid #fbbf24;">
            <h3 style="color: #d97706; margin-top: 0;">🔄 Alternative:</h3>
            <p>{alternative_recommendation}</p>
        </div>

        <div style="background-color: #ecfdf5; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <p><strong>Intensität:</strong> <span style="color: #059669; font-size: 1.2em; font-weight: bold;">{intensity}/10</span></p>
            <hr style="border: none; border-top: 1px solid #d1fae5; margin: 15px 0;">
            <p><strong>Begründung:</strong></p>
            <p style="color: #666; font-style: italic;">{reasoning}</p>
        </div>

        <hr style="border: none; border-top: 1px solid #ccc; margin: 30px 0;">
        <p style="text-align: center; font-size: 12px; color: #999;">Fitness-Coach powered by Garmin + AI | PersonalGarminAICoach</p>
    </body>
    </html>
    """
    return html_body


def send_email(
    subject: str,
    body_text: str,
    body_html: str | None = None,
    recipient_email: str | None = None,
    attach_image: bool = True,
) -> tuple[bool, str]:
    """Send an HTML email with optional image attachment."""
    load_dotenv()
    
    username = os.getenv("MAIL_USERNAME", "").strip()
    password = os.getenv("MAIL_PASSWORD", "").strip()
    
    if not username or not password:
        return False, "MAIL_USERNAME oder MAIL_PASSWORD nicht in .env gesetzt."
    
    if not recipient_email:
        recipient_email = username
    
    try:
        # Build message
        msg = MIMEMultipart("related")
        msg["Subject"] = subject
        msg["From"] = username
        msg["To"] = recipient_email
        
        # Add text and HTML versions
        msg_alternative = MIMEMultipart("alternative")
        msg.attach(msg_alternative)
        msg_alternative.attach(MIMEText(body_text, "plain"))
        
        if body_html:
            msg_alternative.attach(MIMEText(body_html, "html"))
        
        # Embed image if requested
        if attach_image:
            image_path = Path(__file__).resolve().parent.parent / "images" / "fit_heart.png"
            if image_path.exists():
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
        
        # Send email
        smtp = smtplib.SMTP("smtp.web.de", 587, timeout=20)
        smtp.starttls()
        smtp.login(username, password)
        smtp.sendmail(username, recipient_email, msg.as_string())
        smtp.quit()
        
        return True, f"Email an {recipient_email} gesendet."
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP-Authentifizierung fehlgeschlagen. Prüfe MAIL_USERNAME und MAIL_PASSWORD."
    except smtplib.SMTPException as exc:
        return False, f"SMTP-Fehler: {exc}"
    except Exception as exc:
        return False, f"Email-Versand fehlgeschlagen: {exc}"



def notify_recommendation(
    recommendation: dict[str, Any],
    profile: dict[str, Any],
    daily_stats: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Send Discord and/or Email notifications for newly generated model recommendations."""
    result = {"sent": [], "errors": [], "skipped": []}

    if str(recommendation.get("source", "")).lower() != "model":
        result["skipped"].append("Keine neue Modell-Empfehlung; kein Versand.")
        return result

    enriched_recommendation = dict(recommendation)
    latest_day = {}
    if isinstance(daily_stats, dict) and daily_stats:
        latest_key = sorted(daily_stats.keys())[-1]
        latest_day = daily_stats.get(latest_key, {}) if isinstance(daily_stats.get(latest_key, {}), dict) else {}
    enriched_recommendation["latest_day"] = latest_day

    body_text = _build_message(enriched_recommendation)
    body_html = _build_message_html(enriched_recommendation)
    discord_enabled = bool(profile.get("notify_discord", False))
    email_enabled = bool(profile.get("notify_email", False))
    email_address = _resolve_email_recipient(profile)
    discord_user_id = _resolve_discord_recipient(profile)

    # Send Discord notification
    if discord_enabled:
        if discord_user_id:
            success, msg = send_discord_dm(body_text, discord_user_id)
            (result["sent"] if success else result["errors"]).append(msg)
        else:
            result["errors"].append("Discord-Benachrichtigung aktiviert, aber keine Discord-ID gespeichert.")
    else:
        result["skipped"].append("Discord-Benachrichtigung deaktiviert.")

    # Send Email notification
    if email_enabled and email_address:
        success, msg = send_email(
            subject="PersonalGarminAICoach - Tägliche Trainingsempfehlung",
            body_text=body_text,
            body_html=body_html,
            recipient_email=email_address,
            attach_image=True,
        )
        (result["sent"] if success else result["errors"]).append(msg)
    elif email_enabled and not email_address:
        result["errors"].append("Email-Benachrichtigung aktiviert, aber keine Email-Adresse gespeichert.")
    else:
        result["skipped"].append("Email-Benachrichtigung deaktiviert.")

    return result
