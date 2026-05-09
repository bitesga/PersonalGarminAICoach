"""Notification utilities for coach recommendations."""

from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from datetime import datetime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _normalize_language(language: str | None) -> str:
    value = str(language or "en").strip().lower()
    return "de" if value.startswith("de") else "en"


def _tr(language: str, english: str, german: str) -> str:
    return german if _normalize_language(language) == "de" else english


def _get_notification_language(recommendation: dict[str, Any], profile: dict[str, Any] | None = None) -> str:
    profile_lang = ""
    if isinstance(profile, dict):
        profile_lang = str(profile.get("ui_language", "")).strip().lower()
    rec_lang = str(recommendation.get("language", "")).strip().lower()
    return _normalize_language(profile_lang or rec_lang or "en")


def _build_message(recommendation: dict[str, Any], language: str = "en") -> str:
    language = _normalize_language(language)
    title = str(recommendation.get("title") or _tr(language, "Coach Recommendation", "Coach-Empfehlung"))
    intensity = recommendation.get("intensity", "n/a")
    recommendation_text = str(recommendation.get("recommendation") or "")
    alternative_text = str(recommendation.get("alternative") or "").strip()
    reasoning = str(recommendation.get("reasoning") or "")
    latest_day = recommendation.get("latest_day", {}) if isinstance(recommendation.get("latest_day", {}), dict) else {}
    sleep_score = latest_day.get("sleep_score", "n/a")
    body_battery = latest_day.get("body_battery", "n/a")
    stress = latest_day.get("stress", "n/a")
    vo2_max = latest_day.get("vo2_max", "n/a")
    resting_hr = latest_day.get("resting_heart_rate", "n/a")

    if not alternative_text and "Alternative:" in recommendation_text:
        main_recommendation, alternative_recommendation = recommendation_text.split("Alternative:", 1)
        main_recommendation = main_recommendation.strip().rstrip(".")
        alternative_recommendation = alternative_recommendation.strip().rstrip(".")
    elif alternative_text:
        main_recommendation = recommendation_text.strip()
        alternative_recommendation = alternative_text.strip().rstrip(".")
    else:
        main_recommendation = recommendation_text.strip()
        alternative_recommendation = _tr(language, "No alternative provided.", "Keine Alternative angegeben.")

    todays_metrics_label = _tr(language, "TODAY'S METRICS", "HEUTIGE WERTE")

    body = (
        f"{_tr(language, 'GOOD MORNING!', 'GUTEN MORGEN!')}\n\n"
        f"{todays_metrics_label}:\n"
        f"{_tr(language, 'SLEEP SCORE', 'SCHLAF-SCORE')}: {sleep_score}/100\n"
        f"{_tr(language, 'BODY BATTERY', 'KOERPERBATTERIE')}: {body_battery}/100\n"
        f"{_tr(language, 'STRESS', 'STRESS')}: {stress}\n"
        f"VO2MAX: {vo2_max}\n"
        f"RHR: {resting_hr}\n\n"
        f"{_tr(language, 'MAIN RECOMMENDATION', 'HAUPTEMPFEHLUNG')}: {title}\n{main_recommendation}\n\n"
        f"{_tr(language, 'ALTERNATIVE', 'ALTERNATIVE')}:\n{alternative_recommendation}\n\n"
        f"{_tr(language, 'INTENSITY', 'INTENSITAET')}: {intensity}/10\n\n"
        f"{_tr(language, 'REASONING', 'BEGRUENDUNG')}:\n{reasoning}\n"
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
        return False, "DISCORD_BOT_TOKEN is missing."
    if not user_id:
        return False, "Discord user ID is missing."

    try:
        channel_resp = _discord_api_post(
            "https://discord.com/api/v10/users/@me/channels",
            {"recipient_id": user_id},
            token,
        )
        channel_id = str(channel_resp.get("id", "")).strip()
        if not channel_id:
            return False, "Discord DM channel could not be created."

        _discord_api_post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            {"content": message[:1900]},
            token,
        )
        return True, "Discord DM sent."
    except urllib.error.HTTPError as exc:
        try:
            error_payload = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            error_payload = ""
        return False, f"Discord API error {exc.code}: {error_payload or exc.reason}"
    except Exception as exc:
        return False, f"Discord send failed: {exc}"


def _clip(value: Any, max_len: int) -> str:
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _split_recommendation_text(recommendation_text: str, language: str = "en") -> tuple[str, str]:
    if "Alternative:" in recommendation_text:
        main_recommendation, alternative_recommendation = recommendation_text.split("Alternative:", 1)
        main_recommendation = main_recommendation.strip().rstrip(".")
        alternative_recommendation = alternative_recommendation.strip().rstrip(".")
        return main_recommendation, alternative_recommendation
    return recommendation_text.strip(), _tr(language, "No alternative provided.", "Keine Alternative angegeben.")


def _build_discord_recommendation_embed(recommendation: dict[str, Any], language: str = "en") -> dict[str, Any]:
    language = _normalize_language(language)
    title = _clip(recommendation.get("title") or _tr(language, "Coach Recommendation", "Coach-Empfehlung"), 256)
    intensity = _clip(recommendation.get("intensity") or "n/a", 32)
    recommendation_text = str(recommendation.get("recommendation") or "")
    alternative_text = str(recommendation.get("alternative") or "")
    reasoning = _clip(recommendation.get("reasoning") or "", 1024)

    latest_day = recommendation.get("latest_day", {}) if isinstance(recommendation.get("latest_day", {}), dict) else {}
    sleep_score = _clip(latest_day.get("sleep_score", "n/a"), 24)
    body_battery = _clip(latest_day.get("body_battery", "n/a"), 24)
    stress = _clip(latest_day.get("stress", "n/a"), 24)
    vo2_max = _clip(latest_day.get("vo2_max", "n/a"), 24)
    resting_hr = _clip(latest_day.get("resting_heart_rate", "n/a"), 24)

    if alternative_text:
        main_reco, alt_reco = recommendation_text.strip(), alternative_text.strip()
    else:
        main_reco, alt_reco = _split_recommendation_text(recommendation_text, language=language)

    description = _clip(
        f"**{_tr(language, 'Main Recommendation', 'Hauptempfehlung')}**\n{main_reco}\n\n**{_tr(language, 'Alternative', 'Alternative')}**\n{alt_reco}",
        4096,
    )

    embed: dict[str, Any] = {
        "title": f"PersonalGarminAICoach · {title}",
        "description": description,
        "color": 0x38BDF8,
        "fields": [
            {"name": _tr(language, "Sleep Score", "Schlaf-Score"), "value": f"{sleep_score}/100", "inline": True},
            {"name": _tr(language, "Body Battery", "Koerperbatterie"), "value": f"{body_battery}/100", "inline": True},
            {"name": _tr(language, "Stress", "Stress"), "value": f"{stress}", "inline": True},
            {"name": "VO2Max", "value": f"{vo2_max}", "inline": True},
            {"name": "RHR", "value": f"{resting_hr}", "inline": True},
            {"name": _tr(language, "Intensity", "Intensitaet"), "value": f"{intensity}/10", "inline": True},
            {"name": _tr(language, "Reasoning", "Begruendung"), "value": reasoning or "-", "inline": False},
        ],
        "footer": {"text": "Garmin + AI · PersonalGarminAICoach"},
        "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    return embed


def send_discord_dm_embed(
    *,
    user_id: str,
    embed: dict[str, Any],
    content: str | None = None,
) -> tuple[bool, str]:
    """Send a Discord DM with an embed payload.

    Uses the bot token; falls back to standard error handling.
    """
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        return False, "DISCORD_BOT_TOKEN is missing."
    if not user_id:
        return False, "Discord user ID is missing."

    try:
        channel_resp = _discord_api_post(
            "https://discord.com/api/v10/users/@me/channels",
            {"recipient_id": user_id},
            token,
        )
        channel_id = str(channel_resp.get("id", "")).strip()
        if not channel_id:
            return False, "Discord DM channel could not be created."

        payload: dict[str, Any] = {
            "embeds": [embed],
        }
        if content:
            payload["content"] = _clip(content, 1900)

        _discord_api_post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            payload,
            token,
        )
        return True, "Discord embed sent."
    except urllib.error.HTTPError as exc:
        try:
            error_payload = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            error_payload = ""
        return False, f"Discord API error {exc.code}: {error_payload or exc.reason}"
    except Exception as exc:
        return False, f"Discord send failed: {exc}"


def send_discord_recommendation(recommendation: dict[str, Any], user_id: str, fallback_text: str, language: str = "en") -> tuple[bool, str]:
    """Send a daily recommendation as a Discord embed, with plain-text fallback."""
    embed = _build_discord_recommendation_embed(recommendation, language=language)
    ok, msg = send_discord_dm_embed(user_id=user_id, embed=embed)
    if ok:
        return True, msg

    # Fallback to plain DM (some guild DM settings or payload issues can reject embeds)
    ok2, msg2 = send_discord_dm(fallback_text, user_id)
    if ok2:
        return True, f"Embed failed ({msg}) - fallback text sent."
    return False, f"Embed failed ({msg}) and text fallback also failed ({msg2})."


def send_verification_dm(user_id: str, code: str, invite_link: str | None = None) -> tuple[bool, str]:
    """Send a short verification DM containing the code and optional server invite instructions."""
    if not user_id:
        return False, "Discord user ID is missing."
    try:
        message = (
            f"Your verification code: {code}\n\n"
            "Enter this code in the app to verify your account."
            "Note: The bot only sends this code and a short instruction."
        )
        return send_discord_dm(message, user_id)
    except Exception as exc:
        return False, f"Error sending verification code: {exc}"


def _build_message_html(recommendation: dict[str, Any], language: str = "en") -> str:
    """Build an HTML-formatted recommendation message."""
    language = _normalize_language(language)
    title = str(recommendation.get("title") or _tr(language, "Coach Recommendation", "Coach-Empfehlung"))
    intensity = recommendation.get("intensity", "n/a")
    recommendation_text = str(recommendation.get("recommendation") or "")
    alternative_text = str(recommendation.get("alternative") or "").strip()
    reasoning = str(recommendation.get("reasoning") or "")
    latest_day = recommendation.get("latest_day", {}) if isinstance(recommendation.get("latest_day", {}), dict) else {}
    sleep_score = latest_day.get("sleep_score", "n/a")
    body_battery = latest_day.get("body_battery", "n/a")
    stress = latest_day.get("stress", "n/a")
    vo2_max = latest_day.get("vo2_max", "n/a")
    resting_hr = latest_day.get("resting_heart_rate", "n/a")

    if not alternative_text and "Alternative:" in recommendation_text:
        main_recommendation, alternative_recommendation = recommendation_text.split("Alternative:", 1)
        main_recommendation = main_recommendation.strip().rstrip(".")
        alternative_recommendation = alternative_recommendation.strip().rstrip(".")
    elif alternative_text:
        main_recommendation = recommendation_text.strip()
        alternative_recommendation = alternative_text.strip().rstrip(".")
    else:
        main_recommendation = recommendation_text.strip()
        alternative_recommendation = _tr(language, "No alternative provided.", "Keine Alternative angegeben.")

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <div style="text-align: center; margin-bottom: 30px;">
            <img src="cid:fit_heart" alt="Fitness Heart Logo" style="width: 120px; height: auto;">
        </div>
        <h1 style="color: #38bdf8; text-align: center;">PersonalGarminAICoach</h1>
        
        <div style="background-color: #f0f9ff; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h2 style="color: #38bdf8; margin-top: 0;">{_tr(language, 'Good morning! 🏃', 'Guten Morgen! 🏃')}</h2>
            <p style="margin: 10px 0;"><strong>{_tr(language, "Today's metrics", 'Heutige Werte')}:</strong></p>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc;"><strong>{_tr(language, 'Sleep Score', 'Schlaf-Score')}:</strong></td>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc; color: #16a34a; font-weight: bold;">{sleep_score}/100</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc;"><strong>{_tr(language, 'Body Battery', 'Koerperbatterie')}:</strong></td>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc; color: #16a34a; font-weight: bold;">{body_battery}/100</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc;"><strong>{_tr(language, 'Stress', 'Stress')}:</strong></td>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc; color: #16a34a; font-weight: bold;">{stress}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc;"><strong>VO2Max:</strong></td>
                    <td style="padding: 8px; border-bottom: 1px solid #ccc; color: #16a34a; font-weight: bold;">{vo2_max}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>{_tr(language, 'Resting HR', 'Ruhepuls')}:</strong></td>
                    <td style="padding: 8px; color: #16a34a; font-weight: bold;">{resting_hr}</td>
                </tr>
            </table>
        </div>

        <div style="background-color: #f5f3ff; padding: 20px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid #a78bfa;">
            <h2 style="color: #7c3aed; margin-top: 0;">{_tr(language, 'Main Recommendation', 'Hauptempfehlung')}: {title}</h2>
            <p>{main_recommendation}</p>
        </div>

        <div style="background-color: #fef3c7; padding: 20px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid #fbbf24;">
            <h3 style="color: #d97706; margin-top: 0;">{_tr(language, 'Alternative', 'Alternative')}:</h3>
            <p>{alternative_recommendation}</p>
        </div>

        <div style="background-color: #ecfdf5; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <p><strong>{_tr(language, 'Intensity', 'Intensitaet')}:</strong> <span style="color: #059669; font-size: 1.2em; font-weight: bold;">{intensity}/10</span></p>
            <hr style="border: none; border-top: 1px solid #d1fae5; margin: 15px 0;">
            <p><strong>{_tr(language, 'Reasoning', 'Begruendung')}:</strong></p>
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
        return False, "MAIL_USERNAME or MAIL_PASSWORD is not set in .env."
    
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
        
        return True, f"Email sent to {recipient_email}."
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed. Check MAIL_USERNAME and MAIL_PASSWORD."
    except smtplib.SMTPException as exc:
        return False, f"SMTP error: {exc}"
    except Exception as exc:
        return False, f"Email send failed: {exc}"



def notify_recommendation(
    recommendation: dict[str, Any],
    profile: dict[str, Any],
    daily_stats: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Send Discord and/or Email notifications for newly generated model recommendations."""
    result = {"sent": [], "errors": [], "skipped": []}

    if str(recommendation.get("source", "")).lower() != "model":
        result["skipped"].append("No new model recommendation; nothing sent.")
        return result

    language = _get_notification_language(recommendation, profile)

    enriched_recommendation = dict(recommendation)
    latest_day = {}
    if isinstance(daily_stats, dict) and daily_stats:
        latest_key = sorted(daily_stats.keys())[-1]
        latest_day = daily_stats.get(latest_key, {}) if isinstance(daily_stats.get(latest_key, {}), dict) else {}
    enriched_recommendation["latest_day"] = latest_day

    body_text = _build_message(enriched_recommendation, language=language)
    body_html = _build_message_html(enriched_recommendation, language=language)
    discord_enabled = bool(profile.get("notify_discord", False))
    email_enabled = bool(profile.get("notify_email", False))
    email_address = _resolve_email_recipient(profile)
    discord_user_id = _resolve_discord_recipient(profile)

    # Send Discord notification
    if discord_enabled:
        if discord_user_id:
            success, msg = send_discord_recommendation(enriched_recommendation, discord_user_id, fallback_text=body_text, language=language)
            (result["sent"] if success else result["errors"]).append(msg)
        else:
            result["errors"].append("Discord notifications are enabled, but no Discord ID is saved.")
    else:
        result["skipped"].append("Discord notifications are disabled.")

    # Send Email notification
    if email_enabled and email_address:
        success, msg = send_email(
            subject=_tr(language, "PersonalGarminAICoach - Daily training recommendation", "PersonalGarminAICoach - Taegliche Trainingsempfehlung"),
            body_text=body_text,
            body_html=body_html,
            recipient_email=email_address,
            attach_image=True,
        )
        (result["sent"] if success else result["errors"]).append(msg)
    elif email_enabled and not email_address:
        result["errors"].append("Email notifications are enabled, but no email address is saved.")
    else:
        result["skipped"].append("Email notifications are disabled.")

    return result
