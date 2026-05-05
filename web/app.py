from __future__ import annotations

import json
import os
import random
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT_DIR / "core"
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from core import coach_agent
from core import auto_recommendation
from core.data_persistence import (
    load_activities,
    load_garmin_credentials,
    load_daily_stats,
    delete_daily_stat,
    delete_activity,
    load_user_profile,
    save_user_profile,
    save_garmin_credentials,
    save_daily_stats,
    save_activities,
)
from core.notification_service import notify_recommendation, send_email
from core import user_management as user_management
from core import data_entry
from core.notification_service import send_verification_dm
from web.auth import render_auth_gate
from web.sidebar import init_state as init_sidebar_state, render_sidebar as render_sidebar_module
from datetime import datetime, timedelta


LOGO_PATH = ROOT_DIR / "images" / "fit_heart.png"

st.set_page_config(
    page_title="Personal Garmin AI Coach",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "🏁",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
:root {
  --bg: #07111f;
  --panel: rgba(15, 23, 42, 0.78);
  --panel-soft: rgba(30, 41, 59, 0.68);
  --border: rgba(148, 163, 184, 0.18);
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --accent-2: #f59e0b;
}

.stApp {
  background:
    radial-gradient(circle at top left, rgba(56, 189, 248, 0.18), transparent 28%),
    radial-gradient(circle at bottom right, rgba(245, 158, 11, 0.12), transparent 24%),
    linear-gradient(180deg, #06101d 0%, #0b1728 60%, #08111c 100%);
  color: var(--text);
}

.block-container {
    padding-top: 4.4rem;
  padding-bottom: 2.5rem;
}

.hero {
  padding: 1.5rem 1.6rem;
  border: 1px solid var(--border);
  border-radius: 22px;
  background: linear-gradient(135deg, rgba(15, 23, 42, 0.88), rgba(2, 6, 23, 0.72));
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.24);
}

.hero h1 {
  color: var(--text);
  margin-bottom: 0.35rem;
}

.hero p {
  color: var(--muted);
  margin-bottom: 0;
}

.card {
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 1rem 1.1rem;
  background: var(--panel);
  box-shadow: 0 14px 38px rgba(0, 0, 0, 0.2);
}

.card-soft {
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 0.9rem 1rem;
  background: var(--panel-soft);
}

.small-label {
  color: var(--muted);
  font-size: 0.83rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.metric-note {
  color: var(--muted);
  font-size: 0.92rem;
}

.reco-title {
  font-size: 1.45rem;
  font-weight: 700;
  margin-bottom: 0.25rem;
}

.reco-meta {
  color: var(--muted);
  font-size: 0.9rem;
}

.reco-box {
  border-left: 4px solid var(--accent);
  padding-left: 1rem;
}

.section-title {
  margin: 0 0 0.5rem 0;
}
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


DASHBOARD_DEFAULTS: dict[str, Any] = {
    "mobility": "Healthy",
    "preference": "",
    "goal": "Build Strength and Endurance",
    "notify_discord": False,
    "discord_user_id": "",
    "notify_email": False,
    "email": "",
    "linked_email": "",
    "linked_discord_id": "",
    "auto_recommendation_enabled": False,
    "auto_recommendation_times": ["09:00", "15:00"],
}

MOBILITY_OPTIONS = ["Healthy", "Wheelchair", "Minor limitations"]
GOAL_OPTIONS = ["Build Strength and Endurance", "Endurance Focus", "Strength Focus"]


def _request_verification_compat(discord_id: str) -> dict[str, Any]:
    """Request a verification code with backward compatibility for older modules."""
    if hasattr(user_management, "request_verification"):
        return user_management.request_verification(discord_id)

    # Fallback path if an older user_management module is loaded.
    user = user_management.register_user(discord_id)
    code = user.get("verification_code")
    if user.get("verified") or not code:
        refreshed_code = f"{random.randint(100000, 999999)}"
        updated = user_management.update_user(
            discord_id,
            {
                "verified": False,
                "verification_code": refreshed_code,
                "verified_at": None,
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        if isinstance(updated, dict):
            return updated
        user["verified"] = False
        user["verification_code"] = refreshed_code
        user["verified_at"] = None
    return user


def _to_number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_duration(seconds: float) -> str:
    """Format duration in seconds to SS / MM:SS / H:MM:SS format."""
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "n/a"
    total_secs = int(round(seconds))
    minutes = total_secs // 60
    secs = total_secs % 60
    if minutes == 0:
        return f"{secs}s"
    if minutes < 60:
        return f"{minutes}:{secs:02d}"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}:{mins:02d}:{secs:02d}"


def _format_distance(meters: float) -> str:
    """Format distance from meters to kilometers with max 2 decimal places."""
    if not isinstance(meters, (int, float)) or meters < 0:
        return "n/a"
    km = meters / 1000.0
    return f"{km:.2f} km"


def _format_training_effect(activity_type: str, primary_metric: Any) -> str:
    """Format training effect with activity type context."""
    activity_type = str(activity_type or "").lower()
    
    if "strength" in activity_type or "training" in activity_type:
        # For strength training, primary_metric is an exercise list
        if isinstance(primary_metric, list):
            return ", ".join(str(x) for x in primary_metric)
        return str(primary_metric)[:120] if primary_metric else "n/a"
    
    # For cardio/endurance, primary_metric is training effect score (Garmin scale)
    try:
        effect = float(primary_metric)
        effect_label = "Anaerobic" if effect >= 5.0 else "Aerobic"
        return f"{effect_label} ({effect:.2f})"
    except (TypeError, ValueError):
        return str(primary_metric)[:40] if primary_metric else "n/a"


def _get_last_fetch_timestamp() -> str:
    """Get the last updated timestamp from activities.json."""
    from pathlib import Path
    data_dir = Path(__file__).resolve().parents[1] / "data"
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



def _latest_day(daily_stats: dict[str, Any]) -> dict[str, Any]:
    if not daily_stats:
        return {}
    latest_key = sorted(daily_stats.keys())[-1]
    latest = daily_stats.get(latest_key, {})
    return latest if isinstance(latest, dict) else {}


def _normalize_choice(value: Any, options: list[str], default_value: str) -> str:
    candidate = str(value).strip()
    if candidate in options:
        return candidate
    lowered = candidate.lower()
    if options == MOBILITY_OPTIONS:
        if "wheelchair" in lowered:
            return "Wheelchair"
        if "limitation" in lowered:
            return "Minor limitations"
        return "Healthy"
    if options == GOAL_OPTIONS:
        if (
            "build strength and endurance" in lowered
            or "strength and endurance" in lowered
        ):
            return "Build Strength and Endurance"
        if "endurance" in lowered or "marathon" in lowered:
            return "Endurance Focus"
        if "strength" in lowered:
            return "Strength Focus"
    return default_value



def _init_state(user_id: str) -> None:
    profile = load_user_profile(user_id=user_id)
    normalized_mobility = _normalize_choice(profile.get("mobility", DASHBOARD_DEFAULTS["mobility"]), MOBILITY_OPTIONS, DASHBOARD_DEFAULTS["mobility"])
    normalized_goal = _normalize_choice(profile.get("goal", DASHBOARD_DEFAULTS["goal"]), GOAL_OPTIONS, DASHBOARD_DEFAULTS["goal"])
    # Use setdefault to avoid modifying widget state after widget instantiation
    st.session_state.setdefault("mobility_config", normalized_mobility)
    st.session_state.setdefault("goal_config", normalized_goal)
    st.session_state.setdefault("preference_config", str(profile.get("preference", DASHBOARD_DEFAULTS["preference"])).strip())
    st.session_state.setdefault("notify_discord_config", bool(profile.get("notify_discord", DASHBOARD_DEFAULTS["notify_discord"])))
    st.session_state.setdefault("discord_user_id_config", str(profile.get("discord_user_id", DASHBOARD_DEFAULTS["discord_user_id"])).strip())
    st.session_state.setdefault("notify_email_config", bool(profile.get("notify_email", DASHBOARD_DEFAULTS["notify_email"])))
    st.session_state.setdefault("email_config", str(profile.get("email", DASHBOARD_DEFAULTS["email"])).strip())
    # simplify: use email and discord_user_id directly; link targets start empty
    st.session_state.setdefault("link_email_target_config", "")
    st.session_state.setdefault("link_discord_target_config", "")
    st.session_state.setdefault("link_email_code_config", "")
    st.session_state.setdefault("link_discord_code_config", "")
    if "refresh_recommendation" not in st.session_state:
        st.session_state.refresh_recommendation = False
    if "trigger_notification_on_refresh" not in st.session_state:
        st.session_state.trigger_notification_on_refresh = False
    st.session_state.setdefault("coach_status_lines", ["Ready."])
    st.session_state.setdefault("coach_status_level", "info")
    # Verification state: check if user has verified discord_user_id
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


def _set_flash_message(text: str, level: str = "success") -> None:
    st.session_state["ui_flash_text"] = text
    st.session_state["ui_flash_level"] = level


def _render_flash_message() -> None:
    text = str(st.session_state.pop("ui_flash_text", "")).strip()
    if not text:
        return
    level = str(st.session_state.pop("ui_flash_level", "success"))
    if level == "error":
        st.error(text)
    elif level == "warning":
        st.warning(text)
    else:
        st.success(text)



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


def _reload_garmin_data(user_id: str) -> tuple[bool, str]:
    script_path = CORE_DIR / "fetch_garmin_data.py"
    command = [sys.executable, str(script_path), "--user-id", user_id]

    try:
        result = subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT_DIR), check=False)
    except Exception as exc:
        return False, f"Reload failed: {exc}"

    output_parts = []
    if result.stdout.strip():
        output_parts.append(result.stdout.strip())
    if result.stderr.strip():
        output_parts.append(result.stderr.strip())

    combined_output = "\n\n".join(output_parts) if output_parts else "Garmin data was refreshed."
    return result.returncode == 0, combined_output



def _build_profile() -> coach_agent.CoachProfile:
    return coach_agent.CoachProfile(
        mobility=st.session_state.mobility_config,
        preference=st.session_state.preference_config,
        goal=st.session_state.goal_config,
    )


def _invoke_get_coach_recommendation(profile, daily_stats, activities, refresh: bool, user_id: str | None):
    """Call coach_agent.get_coach_recommendation with backward-compatibility for older signatures.

    If the function supports a `user_id` parameter, pass it; otherwise call without it.
    """
    import inspect

    func = coach_agent.get_coach_recommendation
    try:
        sig = inspect.signature(func)
        if "user_id" in sig.parameters:
            return func(profile=profile, daily_stats=daily_stats, activities=activities, refresh=refresh, user_id=user_id)
    except Exception:
        # If introspection fails, fall back to calling without user_id
        pass
    return func(profile=profile, daily_stats=daily_stats, activities=activities, refresh=refresh)



def _render_sidebar(user_id: str) -> tuple[dict[str, Any], Any]:
    profile = load_user_profile(user_id=user_id) or {}
    registered_via_email = str(user_id).startswith("email:")

    with st.sidebar:
        st.markdown("### Access & Profile")
        st.selectbox(
            "Mobility",
            MOBILITY_OPTIONS,
            key="mobility_config",
            help="Choose the mobility profile that guides training selection.",
        )
        st.selectbox(
            "Training goal",
            GOAL_OPTIONS,
            key="goal_config",
            help="The goal is used to select the most suitable session.",
        )
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
        if reload_clicked:
            _set_coach_status(["Refreshing Garmin data..."], "info")
            with st.spinner("Refreshing Garmin data..."):
                success, message = _reload_garmin_data(user_id)
            if success:
                st.success("Garmin data updated.")
                st.info(f"Last refresh: {_get_last_fetch_timestamp()}")
                _set_coach_status(
                    [
                        "Garmin data updated.",
                        "Re-querying AI...",
                    ],
                    "info",
                )
            else:
                st.error("Garmin data could not be refreshed.")
                _set_coach_status(["Garmin refresh failed.", message], "error")
            with st.expander("Reload output", expanded=not success):
                st.code(message, language="text")
            st.session_state.refresh_recommendation = True
            st.session_state.trigger_notification_on_refresh = True
            st.rerun()
        if refresh_clicked:
            st.session_state.refresh_recommendation = True
            st.session_state.trigger_notification_on_refresh = True
            _set_coach_status(["Querying AI..."], "info")
            st.rerun()

        st.markdown("---")
        st.markdown("### Accounts & Notifications")
        st.markdown("#### Discord")
        # Consider a Discord ID linked when `discord_user_id_config` is present
        discord_already_linked = bool(str(st.session_state.get("discord_user_id_config", "")).strip())
        if registered_via_email:
            if discord_already_linked:
                st.toggle("Send Discord DM", key="notify_discord_config")
                # Show the already linked Discord ID clearly (read-only)
                # Show the already linked Discord ID (read-only)
                st.text_input(
                    "Discord user ID",
                    key="discord_user_id_config",
                    help="Recipient ID for Discord DMs via bot token.",
                    disabled=True,
                )
                st.caption("Discord is already linked.")
            else:
                st.text_input(
                    "Discord user ID to link",
                    key="link_discord_target_config",
                    help="A 6-digit link code will be sent to this Discord ID.",
                )
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
        # Consider email linked when `email_config` is set
        email_already_linked = bool(str(st.session_state.get("email_config", "")).strip())
        if registered_via_email:
            st.toggle("Send email notifications", key="notify_email_config")
            st.text_input(
                "Email address",
                key="email_config",
                help="Email address for daily recommendations with HTML formatting.",
                disabled=email_already_linked,
            )
            st.caption("Registered with email.")
        else:
            if email_already_linked:
                st.toggle("Send email notifications", key="notify_email_config")
                st.text_input("Email address", key="email_config", help="Email address for daily recommendations with HTML formatting.")
                st.caption("Email is already linked.")
            else:
                st.text_input(
                    "Email to link",
                    key="link_email_target_config",
                    help="A 6-digit link code will be sent to this address.",
                )
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
                            text = (
                                f"Your link code for connecting your account is: {code}\n\n"
                                "Enter this code in the app to link your email for notifications."
                            )
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
                            profile["linked_email"] = target_email
                            profile["email"] = target_email
                            profile["notify_email"] = True
                            save_user_profile(profile, user_id=user_id)
                            st.session_state.linked_email_config = target_email
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
            _clear_auth_query_param()
            st.session_state.pop("active_discord_id", None)
            st.session_state.pop("temp_discord_id", None)
            st.session_state.pop("temp_code_input", None)
            st.session_state.pop("temp_code_sent", None)
            st.info("You have been logged out. Please register again.")
            st.rerun()

    return _save_profile_from_sidebar(user_id=user_id), status_box


def _resolve_verify_email_password() -> Any:
    verifier = getattr(user_management, "verify_email_password", None)
    if verifier is not None:
        return verifier

    def _fallback_verify_email_password(email: str, password: str) -> bool:
        user = user_management.get_user_by_email(email)
        if not user:
            return False
        auth = user.get("auth")
        if not isinstance(auth, dict):
            return False
        salt = str(auth.get("salt", "")).strip()
        stored_hash = str(auth.get("password_hash", "")).strip()
        if not salt or not stored_hash:
            return False
        import hashlib

        derived_hash = hashlib.pbkdf2_hmac(
            "sha256",
            str(password).encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        ).hex()
        return derived_hash == stored_hash

    return _fallback_verify_email_password


def _resolve_verify_discord_password() -> Any:
    verifier = getattr(user_management, "verify_discord_password", None)
    if verifier is not None:
        return verifier

    def _fallback_verify_discord_password(discord_id: str, password: str) -> bool:
        user = user_management.get_user(discord_id)
        if not user:
            return False
        auth = user.get("auth")
        if not isinstance(auth, dict):
            return False
        salt = str(auth.get("salt", "")).strip()
        stored_hash = str(auth.get("password_hash", "")).strip()
        if not salt or not stored_hash:
            return False
        import hashlib

        derived_hash = hashlib.pbkdf2_hmac(
            "sha256",
            str(password).encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        ).hex()
        return derived_hash == stored_hash

    return _fallback_verify_discord_password


AUTH_TOKENS_PATH = ROOT_DIR / "data" / "auth_tokens.json"


def _load_auth_tokens() -> dict[str, Any]:
    if not AUTH_TOKENS_PATH.exists():
        return {}
    try:
        payload = json.loads(AUTH_TOKENS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_auth_tokens(tokens: dict[str, Any]) -> None:
    AUTH_TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_TOKENS_PATH.write_text(json.dumps(tokens, indent=2, ensure_ascii=False), encoding="utf-8")


def _issue_auth_token(user_id: str, days: int = 3) -> str:
    token = secrets.token_urlsafe(32)
    tokens = _load_auth_tokens()
    expires_at = (datetime.utcnow() + timedelta(days=days)).isoformat()
    tokens[token] = {
        "user_id": str(user_id).strip(),
        "expires_at": expires_at,
    }
    _save_auth_tokens(tokens)
    return token


def _set_auth_query_param(token: str) -> None:
    try:
        st.query_params["auth"] = token
    except Exception:
        pass


def _clear_auth_query_param() -> None:
    try:
        if "auth" in st.query_params:
            del st.query_params["auth"]
    except Exception:
        pass


def _restore_session_from_token() -> bool:
    try:
        token = st.query_params.get("auth")
    except Exception:
        token = None

    if isinstance(token, list):
        token = token[0] if token else None
    token = str(token).strip() if token else ""
    if not token:
        return False

    tokens = _load_auth_tokens()
    record = tokens.get(token)
    if not isinstance(record, dict):
        return False

    user_id = str(record.get("user_id", "")).strip()
    expires_at_raw = str(record.get("expires_at", "")).strip()
    if not user_id or not expires_at_raw:
        return False

    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except Exception:
        return False

    if expires_at < datetime.utcnow():
        tokens.pop(token, None)
        _save_auth_tokens(tokens)
        _clear_auth_query_param()
        return False

    st.session_state.discord_verified = True
    st.session_state.active_discord_id = user_id
    return True


def _persist_auth_session(user_id: str) -> None:
    token = _issue_auth_token(user_id=user_id, days=3)
    _set_auth_query_param(token)



def _render_summary_cards(daily_stats: dict[str, Any], activities: list[dict[str, Any]]) -> None:
    latest = _latest_day(daily_stats)
    sleep_score = _to_number(latest.get("sleep_score"))
    body_battery = _to_number(latest.get("body_battery"))
    stress = _to_number(latest.get("stress"))
    vo2_max = _to_number(latest.get("vo2_max"))
    resting_hr = _to_number(latest.get("resting_heart_rate"))
    training_load_acute = _to_number(latest.get("training_load_acute"))
    training_balance_feedback = str(latest.get("training_balance_feedback", "N/A")).strip()

    cols = st.columns(6)
    metrics = [
        (
            "Sleep",
            sleep_score,
            "/100",
            "Score from last night. Garmin Sleep Score is 0-100 based on duration, stages (deep, light, REM), stress (HRV), and interruptions.",
        ),
        ("Body 🔋", body_battery, "/100", "Energy available for training."),
        (
            "Stress",
            stress,
            "HRV",
            "Index based on heart rate variability (HRV). Lower variability suggests higher stress.",
        ),
        ("VO2Max", vo2_max, "ml/kg/min", "Indicator of aerobic fitness. Max oxygen uptake."),
        ("RHR", resting_hr, "bpm", "Resting heart rate."),
        ("Acute Load", training_load_acute, "load", "Current training load."),
    ]

    for column, (label, value, suffix, help_text) in zip(cols, metrics):
        with column:
            # Show the main metric value without a delta (no arrow, neutral color)
            if value is None:
                display = "n/a"
            else:
                display = f"{round(value, 1)}" if isinstance(value, (int, float)) else str(value)
            st.metric(label, display, delta=None, help=help_text)
            # Render the unit/suffix as a muted note below the metric
            st.markdown(
                f"<div style='color:#94a3b8; font-size:0.9rem; margin-top:0.15rem'>{suffix}</div>",
                unsafe_allow_html=True,
            )
    st.write("") 
    if label == "Acute Load" and training_balance_feedback and training_balance_feedback != "N/A":
        st.markdown(
            f"""
            <div style='margin-top:0.45rem; padding:0.35rem 0.65rem; border-radius:999px; display:inline-block; background:rgba(56, 189, 248, 0.12); border:1px solid rgba(56, 189, 248, 0.24); color:#e2e8f0; font-size:0.98rem; font-weight:800; letter-spacing:0.02em;'>
                Training Load Balance: {training_balance_feedback}
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")



def _render_activities(activities: list[dict[str, Any]]) -> None:
    st.markdown("<h3 class='section-title'>Recent Activities</h3>", unsafe_allow_html=True)
    last_fetch = _get_last_fetch_timestamp()
    st.caption(f"Last updated: {last_fetch}")
    
    if not activities:
        st.info("No activities found.")
        return

    rows = []
    for activity in activities:
        # Determine display date: prefer combined datetime in activity['date'] if present
        date_val = activity.get("date", "n/a")
        time_val = activity.get("time")
        # If date looks like a plain date and time exists, combine them
        display_date = date_val
        try:
            if isinstance(date_val, str) and len(date_val) == 10 and time_val:
                display_date = f"{date_val} {str(time_val)[:5]}"
        except Exception:
            pass

        rows.append(
            {
                "Date": display_date,
                "Type": activity.get("activity_type", "n/a"),
                "Training Effect": _format_training_effect(
                    activity.get("activity_type", ""),
                    activity.get("primary_metric", "n/a")
                ),
                "Duration": _format_duration(activity.get("duration", "n/a")),
                "Calories": f"{activity.get('calories', 'n/a'):.0f}" if isinstance(activity.get('calories'), (int, float)) else "n/a",
                "Distance": _format_distance(activity.get("distance", "n/a")) if activity.get("distance") else "—",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

def _render_recommendation(recommendation: dict[str, Any]) -> None:
    st.markdown("<h3 class='section-title'>Next Training Recommendation</h3>", unsafe_allow_html=True)
    title = recommendation.get("title") or "Recommendation"
    recommendation_text = recommendation.get("recommendation") or "n/a"
    alternative_text = recommendation.get("alternative") or ""
    intensity = recommendation.get("intensity", "n/a")
    reasoning = recommendation.get("reasoning") or "n/a"
    st.markdown(
        f"""
                <div class='card reco-box'>
                    <div class='reco-title'>{title}</div>
                    <div class='reco-meta'>Intensity {intensity}/10 · Source {recommendation.get('source', 'model')}</div>
                    <p><strong>Recommendation:</strong> {recommendation_text}</p>
                    <p><strong>Alternative:</strong> {alternative_text or '-'}</p>
                    <p><strong>Reasoning:</strong> {reasoning}</p>
                </div>
        """,
        unsafe_allow_html=True,
    )
    
    st.write("")
    st.write("")


def _render_data_sources_tab(profile: dict[str, Any], user_id: str) -> None:
    """Render the Data Sources tab with Garmin OAuth and manual entry forms."""
    st.markdown("## Data Sources")
    st.write("Connect your Garmin device or enter data manually.")
    st.write("")
    _render_flash_message()
    
    # Garmin login section
    existing_credentials = load_garmin_credentials(user_id=user_id)
    if existing_credentials.get("email") and "garmin_email" not in st.session_state:
        st.session_state["garmin_email"] = str(existing_credentials.get("email", ""))

    credentials = data_entry.render_garmin_credentials_section()
    if credentials is not None:
        try:
            save_garmin_credentials(credentials, user_id=user_id)
            _set_flash_message("Garmin account connected and saved.")
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to save Garmin credentials: {exc}")
    st.markdown("---")
    
    # Manual health entry
    st.markdown("### Manual data entry")
    tab_health, tab_activity = st.tabs(["Health metrics", "Activity"])
    
    with tab_health:
        health_data = data_entry.render_manual_health_entry()
        if st.button("Save health metrics", key="save_health_btn"):
            try:
                # Convert date to dict key format
                date_key = health_data.get("date", "")
                health_entry = {k: v for k, v in health_data.items() if k != "date"}
                health_entry["source"] = "manual"
                health_dict = {date_key: health_entry}
                save_daily_stats(health_dict, user_id=user_id)
                _set_flash_message(f"Health metrics saved for {date_key}.")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to save: {exc}")
    
    with tab_activity:
        activity_data = data_entry.render_manual_activity_entry()
        if activity_data is not None:
            try:
                # Load existing activities and append new one
                current_activities = load_activities(user_id=user_id)
                activity_data = dict(activity_data)
                activity_data["source"] = "manual"
                activity_data.setdefault("id", f"manual-{datetime.now().isoformat(timespec='seconds')}")
                current_activities.insert(0, activity_data)
                save_activities(current_activities, user_id=user_id)
                _set_flash_message(f"Activity ({activity_data.get('activity_type')}) saved.")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to save: {exc}")

    st.markdown("---")
    st.markdown("### Delete manual entries")

    current_daily_stats = load_daily_stats(user_id=user_id)
    manual_health_entries = [
        (date_key, entry)
        for date_key, entry in current_daily_stats.items()
        if isinstance(entry, dict) and str(entry.get("source", "")).lower() == "manual"
    ]

    current_activity_entries = load_activities(user_id=user_id)
    manual_activity_entries = [
        activity for activity in current_activity_entries
        if isinstance(activity, dict) and str(activity.get("source", "")).lower() == "manual"
    ]

    delete_col1, delete_col2 = st.columns(2)
    with delete_col1:
        st.markdown("#### Health metrics")
        if manual_health_entries:
            health_labels = [
                f"{date_key} · {str(entry.get('time', ''))[:5] if entry.get('time') else '--'}"
                for date_key, entry in manual_health_entries
            ]
            selected_health_label = st.selectbox("Select entry", health_labels, key="delete_health_select")
            selected_health_index = health_labels.index(selected_health_label)
            selected_health_date = manual_health_entries[selected_health_index][0]
            if st.button("Delete health metrics", key="delete_health_btn"):
                delete_daily_stat(selected_health_date, user_id=user_id)
                _set_flash_message(f"Health metrics deleted for {selected_health_date}.")
                st.rerun()
        else:
            st.info("No manual health metrics available.")

    with delete_col2:
        st.markdown("#### Activities")
        if manual_activity_entries:
            activity_labels = [
                f"{activity.get('date', 'n/a')} · {str(activity.get('time', ''))[:5] if activity.get('time') else '--'} · {activity.get('activity_type', 'n/a')}"
                for activity in manual_activity_entries
            ]
            selected_activity_label = st.selectbox("Select entry", activity_labels, key="delete_activity_select")
            selected_activity_index = activity_labels.index(selected_activity_label)
            selected_activity_id = str(manual_activity_entries[selected_activity_index].get("id", ""))
            if st.button("Delete activity", key="delete_activity_btn"):
                delete_activity(selected_activity_id, user_id=user_id)
                _set_flash_message(f"Activity {selected_activity_label} deleted.")
                st.rerun()
        else:
            st.info("No manual activities available.")


def main() -> None:
    auto_recommendation.start_scheduler()
    active_user_id = render_auth_gate()
    if not active_user_id:
        return

    # ===== USER IS VERIFIED FROM HERE ON =====
    # Initialize full app state AFTER successful verification
    init_sidebar_state(user_id=active_user_id)
    
    daily_stats = load_daily_stats(user_id=active_user_id)
    activities = load_activities(user_id=active_user_id)

    profile, status_box = render_sidebar_module(user_id=active_user_id)
    coach_profile = _build_profile()
    
    # Main tabs
    tab_dashboard, tab_data_sources = st.tabs(["Dashboard", "Data Sources"])
    
    with tab_dashboard:
        refresh = bool(st.session_state.pop("refresh_recommendation", False))
        notify_on_refresh = bool(st.session_state.pop("trigger_notification_on_refresh", False))

        if refresh:
            _set_coach_status(["Querying AI..."], "info")
            _render_coach_status(status_box)
            with st.spinner("Re-querying AI..."):
                recommendation = _invoke_get_coach_recommendation(
                    profile=coach_profile,
                    daily_stats=daily_stats,
                    activities=activities,
                    refresh=True,
                    user_id=active_user_id,
                )
            _set_coach_status(["Loading AI response into the dashboard."], "info")
            _render_coach_status(status_box)
        else:
            recommendation = _invoke_get_coach_recommendation(
                profile=coach_profile,
                daily_stats=daily_stats,
                activities=activities,
                refresh=False,
                user_id=active_user_id,
            )

        if notify_on_refresh:
            _set_coach_status(["Sending notification..."], "info")
            _render_coach_status(status_box)
            try:
                notify_result = notify_recommendation(recommendation, profile, daily_stats=daily_stats)
            except TypeError:
                notify_result = notify_recommendation(recommendation, profile)
            if notify_result["sent"]:
                st.success(" | ".join(notify_result["sent"]))
                _set_coach_status(["Sent: " + " | ".join(notify_result["sent"])], "success")
            for error in notify_result["errors"]:
                st.error(error)
                _set_coach_status(["Error: " + error], "error")

            if not notify_result["sent"] and not notify_result["errors"]:
                skipped = notify_result.get("skipped", [])
                if skipped:
                    _set_coach_status(["Note: " + skipped[0]], "info")

            if recommendation.get("source") == "local":
                reason = str(recommendation.get("fallback_reason", "LLM unavailable or API key missing.")).strip()
                _set_coach_status([f"Local fallback active: {reason}"], "error")

            _render_coach_status(status_box)
        elif refresh:
            if recommendation.get("source") == "local":
                reason = str(recommendation.get("fallback_reason", "LLM unavailable or API key missing.")).strip()
                _set_coach_status([f"Local fallback active: {reason}"], "error")
            else:
                _set_coach_status(["Refresh complete."], "success")
            _render_coach_status(status_box)

        st.markdown(
            "<div class='hero'><h1>Personal Garmin AI Coach</h1><p>Fitness metrics, activities, and today's recommendation in one dashboard. The coach uses a 6-hour cache to save tokens.</p></div>",
            unsafe_allow_html=True,
        )
        st.write("")

        # Only show data and recommendations if we have at least some data
        has_data = bool(daily_stats or activities)
        if not has_data:
            st.info("📊 No data yet. Go to 'Data Sources' and connect Garmin or enter data manually.")
        else:
            _render_summary_cards(daily_stats, activities)
            st.write("")
            _render_recommendation(recommendation)
            _render_activities(activities)
    
    with tab_data_sources:
        st.markdown(f"**Active user:** {active_user_id}")
        st.divider()
        _render_data_sources_tab(profile, user_id=active_user_id)


if __name__ == "__main__":
    main()
