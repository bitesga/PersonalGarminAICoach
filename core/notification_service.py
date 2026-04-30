"""Notification utilities for coach recommendations."""

from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Any


def _build_message(recommendation: dict[str, Any]) -> tuple[str, str]:
    title = str(recommendation.get("titel", "Coach Empfehlung"))
    intensity = recommendation.get("intensitaet", "n/a")
    recommendation_text = str(recommendation.get("empfehlung", ""))
    reasoning = str(recommendation.get("begruendung", ""))

    subject = f"[Garmin AI Coach] {title}"
    body = (
        f"{title}\n"
        f"Intensitaet: {intensity}/10\n\n"
        f"Empfehlung:\n{recommendation_text}\n\n"
        f"Begruendung:\n{reasoning}\n"
    )
    return subject, body


def _discord_api_post(url: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
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


def send_email_notification(subject: str, body: str, to_email: str) -> tuple[bool, str]:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM", smtp_username).strip()
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() not in {"0", "false", "no"}

    if not smtp_host or not smtp_from:
        return False, "SMTP_HOST oder SMTP_FROM fehlt."
    if not to_email:
        return False, "Empfaenger-E-Mail fehlt."

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = to_email
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if smtp_username and smtp_password:
                server.login(smtp_username, smtp_password)
            server.send_message(message)
        return True, "E-Mail gesendet."
    except Exception as exc:
        return False, f"E-Mail-Versand fehlgeschlagen: {exc}"


def notify_recommendation(
    recommendation: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, list[str]]:
    """Send Discord/email notification only for newly generated model recommendations."""
    result = {"sent": [], "errors": [], "skipped": []}

    if str(recommendation.get("source", "")).lower() != "model":
        result["skipped"].append("Keine neue Modell-Empfehlung; kein Versand.")
        return result

    subject, body = _build_message(recommendation)
    discord_enabled = bool(profile.get("notify_discord", False))
    email_enabled = bool(profile.get("notify_email_enabled", False))

    if discord_enabled:
        success, msg = send_discord_dm(body, str(profile.get("discord_user_id", "")).strip())
        (result["sent"] if success else result["errors"]).append(msg)
    else:
        result["skipped"].append("Discord-Benachrichtigung deaktiviert.")

    if email_enabled:
        if not bool(profile.get("email_verified", False)):
            result["errors"].append("E-Mail nicht verifiziert; Versand uebersprungen.")
            return result

        target_email = str(profile.get("email", "")).strip()
        success, msg = send_email_notification(subject, body, target_email)
        (result["sent"] if success else result["errors"]).append(msg)
    else:
        result["skipped"].append("E-Mail-Benachrichtigung deaktiviert.")

    return result
