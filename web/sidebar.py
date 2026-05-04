from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from core.data_persistence import load_user_profile, save_user_profile
from core.notification_service import send_email, send_verification_dm
from core import user_management

ROOT_DIR = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT_DIR / "core"
LOG_PATH = ROOT_DIR / "data" / "app.log"
LOGO_PATH = ROOT_DIR / "images" / "fit_heart.png"

DASHBOARD_DEFAULTS: dict[str, Any] = {
    "mobility": "Healthy",
    "preference": "",
    "goal": "Build Strength and Endurance",
    "notify_discord": False,
    "discord_user_id": "",
    "notify_email": False,
    "email": "",
}

MOBILITY_OPTIONS = ["Healthy", "Wheelchair", "Minor limitations"]
GOAL_OPTIONS = ["Build Strength and Endurance", "Endurance Focus", "Strength Focus"]


def _normalize_choice(value: Any, options: list[str], default_value: str) -> str:
    candidate = str(value).strip()
    if candidate in options:
        return candidate
    lowered = candidate.lower()
    if options == MOBILITY_OPTIONS:
        if "rollstuhl" in lowered or "wheelchair" in lowered:
            return "Wheelchair"
        if "einschr" in lowered or "behind" in lowered or "limitation" in lowered:
            return "Minor limitations"
        return "Healthy"
    if options == GOAL_OPTIONS:
        if (
            "build strength and endurance" in lowered
            or "strength and endurance" in lowered
            or "kraft und ausdauer" in lowered
            or ("kraft" in lowered and "ausdauer" in lowered)
        ):
            return "Build Strength and Endurance"
        if "endurance" in lowered or "ausdauer" in lowered or "marathon" in lowered or "laufen" in lowered:
            return "Endurance Focus"
        if "strength" in lowered or "kraft" in lowered:
            return "Strength Focus"
    return default_value


def _get_last_fetch_timestamp() -> str:
    data_dir = ROOT_DIR / "data"
    activities_file = data_dir / "activities.json"

    if not activities_file.exists():
        return "Never loaded"

    try:
        data = json.loads(activities_file.read_text(encoding="utf-8"))
        last_updated = data.get("last_updated", "")
        if last_updated:
            dt = datetime.fromisoformat(last_updated)
            return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        pass

    return "Unknown"


def _reload_garmin_data(user_id: str) -> tuple[bool, str]:
    script_path = CORE_DIR / "fetch_garmin_data.py"
    command = [sys.executable, str(script_path), "--user-id", user_id]

    try:
        result = subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT_DIR), check=False)
    except Exception as exc:
        return False, f"Reload fehlgeschlagen: {exc}"

    output_parts = []
    if result.stdout.strip():
        output_parts.append(result.stdout.strip())
    if result.stderr.strip():
        output_parts.append(result.stderr.strip())

    combined_output = "\n\n".join(output_parts) if output_parts else "Garmin data was refreshed."
    return result.returncode == 0, combined_output


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("personal_garmin_ai_coach")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def _log_event(level: str, message: str) -> None:
    logger = _get_logger()
    if level == "error":
        logger.error(message)
    elif level == "warning":
        logger.warning(message)
    else:
        logger.info(message)


def _get_config_warnings() -> list[str]:
    warnings: list[str] = []
    if not os.getenv("GROQ_CLOUD_KEY", "").strip():
        warnings.append("GROQ_CLOUD_KEY is missing: AI responses will use local fallback.")
    if not os.getenv("DISCORD_BOT_TOKEN", "").strip():
        warnings.append("DISCORD_BOT_TOKEN is missing: Discord DMs cannot be sent.")
    mail_user = os.getenv("MAIL_USERNAME", "").strip()
    mail_pass = os.getenv("MAIL_PASSWORD", "").strip()
    if not mail_user or not mail_pass:
        warnings.append("MAIL_USERNAME or MAIL_PASSWORD is missing: email sending is unavailable.")
    return warnings


def init_state(user_id: str) -> None:
    profile = load_user_profile(user_id=user_id) or {}
    st.session_state.setdefault("mobility_config", _normalize_choice(profile.get("mobility", DASHBOARD_DEFAULTS["mobility"]), MOBILITY_OPTIONS, DASHBOARD_DEFAULTS["mobility"]))
    st.session_state.setdefault("goal_config", _normalize_choice(profile.get("goal", DASHBOARD_DEFAULTS["goal"]), GOAL_OPTIONS, DASHBOARD_DEFAULTS["goal"]))
    st.session_state.setdefault("preference_config", str(profile.get("preference", DASHBOARD_DEFAULTS["preference"])).strip())
    st.session_state.setdefault("notify_discord_config", bool(profile.get("notify_discord", DASHBOARD_DEFAULTS["notify_discord"])))
    st.session_state.setdefault("discord_user_id_config", str(profile.get("discord_user_id", DASHBOARD_DEFAULTS["discord_user_id"])).strip())
    st.session_state.setdefault("notify_email_config", bool(profile.get("notify_email", DASHBOARD_DEFAULTS["notify_email"])))
    st.session_state.setdefault("email_config", str(profile.get("email", DASHBOARD_DEFAULTS["email"])).strip())
    st.session_state.setdefault("link_email_target_config", "")
    st.session_state.setdefault("link_discord_target_config", "")
    st.session_state.setdefault("link_email_code_config", "")
    st.session_state.setdefault("link_discord_code_config", "")
    if "refresh_recommendation" not in st.session_state:
        st.session_state.refresh_recommendation = False
    if "trigger_notification_on_refresh" not in st.session_state:
        st.session_state.trigger_notification_on_refresh = False
    if "garmin_data_updated" not in st.session_state:
        st.session_state.garmin_data_updated = False
    st.session_state.setdefault("coach_status_lines", ["Ready."])
    st.session_state.setdefault("coach_status_level", "info")
    st.session_state.setdefault("discord_verified", bool(profile.get("discord_user_id", "").strip()))


def _set_coach_status(lines: list[str], level: str = "info") -> None:
    st.session_state.coach_status_lines = lines
    st.session_state.coach_status_level = level


def _render_coach_status(container: Any) -> None:
    lines = st.session_state.get("coach_status_lines", [])
    level = st.session_state.get("coach_status_level", "info")
    message = "\n".join(lines) if lines else "Ready."

    if level == "success":
        container.success(message)
    elif level == "error":
        container.error(message)
    else:
        container.info(message)


def _save_profile_from_sidebar(user_id: str) -> dict[str, Any]:
    profile = load_user_profile(user_id=user_id) or {}
    profile.update({
        "mobility": st.session_state.mobility_config.strip(),
        "preference": st.session_state.preference_config.strip(),
        "goal": st.session_state.goal_config.strip(),
        "notify_discord": bool(st.session_state.notify_discord_config),
        "discord_user_id": st.session_state.discord_user_id_config.strip(),
        "notify_email": bool(st.session_state.notify_email_config),
        "email": st.session_state.email_config.strip(),
    })
    save_user_profile(profile, user_id=user_id)
    return profile


def render_sidebar(user_id: str) -> tuple[dict[str, Any], Any]:
    profile = load_user_profile(user_id=user_id) or {}
    registered_via_email = str(user_id).startswith("email:")

    with st.sidebar:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=88)
        st.markdown("### Access & Profile")
        st.selectbox("Mobility", MOBILITY_OPTIONS, key="mobility_config", help="Choose the mobility profile that guides training selection.")
        st.selectbox("Training goal", GOAL_OPTIONS, key="goal_config", help="The goal is used to select the most suitable session.")
        st.text_area(
            "Other considerations",
            key="preference_config",
            height=96,
            placeholder="e.g., no hard sprints, prefer mornings, outdoor only",
            help="Extra notes the coach should consider.",
        )
        st.markdown("---")
        st.markdown("### Coach")
        reload_clicked = st.button("Refresh Garmin data", use_container_width=True)
        refresh_clicked = st.button("Refresh recommendation (AI)", use_container_width=True)
        status_box = st.empty()
        _render_coach_status(status_box)

        config_warnings = _get_config_warnings()
        if config_warnings:
            st.warning("\n".join(config_warnings))

        if reload_clicked:
            _set_coach_status(["Refreshing Garmin data..."], "info")
            with st.spinner("Refreshing Garmin data..."):
                success, message = _reload_garmin_data(user_id)
            if success:
                st.success("Garmin data updated.")
                st.info(f"Last refresh: {_get_last_fetch_timestamp()}")
                _set_coach_status(["Garmin data updated."], "success")
                st.session_state.garmin_data_updated = True
                _log_event("info", f"Garmin refresh succeeded for user {user_id}.")
            else:
                st.error("Garmin data could not be refreshed.")
                _set_coach_status(["Garmin refresh failed.", message], "error")
                _log_event("error", f"Garmin refresh failed for user {user_id}: {message}")
            with st.expander("Reload output", expanded=not success):
                st.code(message, language="text")
            st.rerun()

        if st.session_state.get("garmin_data_updated"):
            st.info("Data updated. Load a new recommendation for this data?")
            if st.button("Load new recommendation", use_container_width=True, key="refresh_after_reload"):
                st.session_state.garmin_data_updated = False
                st.session_state.refresh_recommendation = True
                st.session_state.trigger_notification_on_refresh = True
                _set_coach_status(["Querying AI..."], "info")
                _log_event("info", f"Recommendation requested after Garmin refresh for user {user_id}.")
                st.rerun()
            if st.button("Not now", use_container_width=True, key="skip_refresh_after_reload"):
                st.session_state.garmin_data_updated = False
                _set_coach_status(["Ready."], "info")
                _log_event("info", f"Recommendation skipped after Garmin refresh for user {user_id}.")
                st.rerun()

        if refresh_clicked:
            st.session_state.refresh_recommendation = True
            st.session_state.trigger_notification_on_refresh = True
            _set_coach_status(["Querying AI..."], "info")
            _log_event("info", f"Manual recommendation refresh requested for user {user_id}.")
            st.rerun()

        st.markdown("---")
        st.markdown("### Accounts & Notifications")
        st.markdown("#### Discord")
        discord_already_linked = bool(str(st.session_state.get("discord_user_id_config", "")).strip())
        if registered_via_email:
            if discord_already_linked:
                st.toggle("Send Discord DM", key="notify_discord_config")
                st.text_input("Discord user ID", key="discord_user_id_config", help="Recipient ID for Discord DMs via bot token.", disabled=True)
                st.caption("Discord is already linked.")
            else:
                st.text_input("Discord user ID to link", key="link_discord_target_config", help="A 6-digit link code will be sent to this Discord ID.")
                if st.button("Send code to Discord", use_container_width=True, key="send_link_discord_code_btn"):
                    target_discord_id = str(st.session_state.link_discord_target_config).strip()
                    if not target_discord_id:
                        st.error("Please enter a Discord user ID.")
                    else:
                        link_user = user_management.request_contact_link(user_id, "discord", target_discord_id)
                        code = str(link_user.get("pending_link", {}).get("verification_code", "")).strip()
                        if not code:
                            st.error("Could not generate a link code.")
                        else:
                            sent, msg = send_verification_dm(target_discord_id, code)
                            if sent:
                                st.success("Link code sent via Discord DM.")
                            else:
                                st.error(f"Discord send failed: {msg}")
                st.text_input("Discord link code", key="link_discord_code_config", help="Enter the 6-digit code from Discord.")
                if st.button("Link Discord", use_container_width=True, key="verify_link_discord_code_btn"):
                    target_discord_id = str(st.session_state.link_discord_target_config).strip()
                    code = str(st.session_state.link_discord_code_config).strip()
                    if not target_discord_id:
                        st.error("Please enter the Discord user ID first.")
                    elif not code:
                        st.error("Please enter the link code.")
                    else:
                        ok = user_management.verify_contact_link(user_id, "discord", target_discord_id, code)
                        if ok:
                            profile = load_user_profile(user_id=user_id) or {}
                            profile["discord_user_id"] = target_discord_id
                            profile["notify_discord"] = True
                            save_user_profile(profile, user_id=user_id)
                            st.session_state.discord_user_id_config = target_discord_id
                            st.session_state.notify_discord_config = True
                            st.success("Discord linked successfully.")
                        else:
                            st.error("Link code is invalid or expired.")
        else:
            st.toggle("Send Discord DM", key="notify_discord_config")
            st.text_input("Discord user ID", key="discord_user_id_config", help="Recipient ID for Discord DMs via bot token.")
            st.caption("Registered with Discord.")

        st.markdown("#### Email")
        email_already_linked = bool(str(st.session_state.get("email_config", "")).strip())
        if registered_via_email:
            st.toggle("Send email notifications", key="notify_email_config")
            st.text_input("Email address", key="email_config", help="Email address for daily recommendations with HTML formatting.", disabled=email_already_linked)
            st.caption("Registered with email.")
        else:
            if email_already_linked:
                st.toggle("Send email notifications", key="notify_email_config")
                st.text_input("Email address", key="email_config", help="Email address for daily recommendations with HTML formatting.")
                st.caption("Email is already linked.")
            else:
                st.text_input("Email to link", key="link_email_target_config", help="A 6-digit link code will be sent to this address.")
                if st.button("Send code to email", use_container_width=True, key="send_link_email_code_btn"):
                    target_email = str(st.session_state.link_email_target_config).strip().lower()
                    if not target_email:
                        st.error("Please enter an email address.")
                    else:
                        link_user = user_management.request_contact_link(user_id, "email", target_email)
                        code = str(link_user.get("pending_link", {}).get("verification_code", "")).strip()
                        if not code:
                            st.error("Could not generate a link code.")
                        else:
                            subject = "Your link code for PersonalGarminAICoach"
                            text = f"Your link code for connecting your account is: {code}\n\nEnter this code in the app to link your email for notifications."
                            html = f"<p>Your link code for connecting your account is: <strong>{code}</strong></p>"
                            sent, msg = send_email(subject=subject, body_text=text, body_html=html, recipient_email=target_email)
                            if sent:
                                st.success("Link code sent via email.")
                            else:
                                st.error(f"Email send failed: {msg}")
                st.text_input("Email link code", key="link_email_code_config", help="Enter the 6-digit code from the email.")
                if st.button("Link email", use_container_width=True, key="verify_link_email_code_btn"):
                    target_email = str(st.session_state.link_email_target_config).strip().lower()
                    code = str(st.session_state.link_email_code_config).strip()
                    if not target_email:
                        st.error("Please enter the email address first.")
                    elif not code:
                        st.error("Please enter the link code.")
                    else:
                        ok = user_management.verify_contact_link(user_id, "email", target_email, code)
                        if ok:
                            profile = load_user_profile(user_id=user_id) or {}
                            profile["email"] = target_email
                            profile["notify_email"] = True
                            save_user_profile(profile, user_id=user_id)
                            st.session_state.email_config = target_email
                            st.session_state.notify_email_config = True
                            st.success("Email linked successfully.")
                        else:
                            st.error("Link code is invalid or expired.")

        st.markdown("---")
        save_clicked = st.button("Save profile", use_container_width=True)
        if save_clicked:
            _save_profile_from_sidebar(user_id=user_id)
            st.success("Profile saved")

        logout_clicked = st.button("Log out", use_container_width=True)
        if logout_clicked:
            st.session_state.discord_verified = False
            st.query_params.pop("auth", None)
            st.session_state.pop("active_discord_id", None)
            st.session_state.pop("temp_discord_id", None)
            st.session_state.pop("temp_code_input", None)
            st.session_state.pop("temp_code_sent", None)
            st.info("You have been logged out. Please register again.")
            st.rerun()

    return _save_profile_from_sidebar(user_id=user_id), status_box
