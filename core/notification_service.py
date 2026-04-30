"""Notification utilities for coach recommendations."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


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
        f"HAUPTEMPFEHLUNG:{title}\n{main_recommendation}\n\n"
        f"ALTERNATIVE:\n{alternative_recommendation}\n\n"
        f"INTENSITAET: {intensity}/10\n\n"
        f"BEGRUENDUNG:\n{reasoning}\n"
    )
    return body


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


def notify_recommendation(
    recommendation: dict[str, Any],
    profile: dict[str, Any],
    daily_stats: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Send Discord notification only for newly generated model recommendations."""
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

    body = _build_message(enriched_recommendation)
    discord_enabled = bool(profile.get("notify_discord", False))

    if discord_enabled:
        success, msg = send_discord_dm(body, str(profile.get("discord_user_id", "")).strip())
        (result["sent"] if success else result["errors"]).append(msg)
    else:
        result["skipped"].append("Discord-Benachrichtigung deaktiviert.")

    return result
