from __future__ import annotations

import json
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

DASHBOARD_DEFAULTS: dict[str, Any] = {
    "mobility": "Gesund",
    "preference": "",
    "goal": "Kraft und Ausdauer maximieren",
    "notify_discord": False,
    "discord_user_id": "",
    "notify_email": False,
    "email": "",
}

MOBILITY_OPTIONS = ["Gesund", "Rollstuhl", "Leichte Einschränkungen"]
GOAL_OPTIONS = ["Kraft und Ausdauer maximieren", "Ausdauer Fokus", "Kraft Fokus"]


def _normalize_choice(value: Any, options: list[str], default_value: str) -> str:
    candidate = str(value).strip()
    if candidate in options:
        return candidate
    lowered = candidate.lower()
    if options == MOBILITY_OPTIONS:
        if "rollstuhl" in lowered or "wheelchair" in lowered:
            return "Rollstuhl"
        if "einschr" in lowered or "behind" in lowered:
            return "Leichte Einschränkungen"
        return "Gesund"
    if options == GOAL_OPTIONS:
        if "kraft und ausdauer" in lowered or ("kraft" in lowered and "ausdauer" in lowered):
            return "Kraft und Ausdauer maximieren"
        if "ausdauer" in lowered or "marathon" in lowered or "laufen" in lowered:
            return "Ausdauer Fokus"
        if "kraft" in lowered:
            return "Kraft Fokus"
    return default_value


def _get_last_fetch_timestamp() -> str:
    data_dir = ROOT_DIR / "data"
    activities_file = data_dir / "activities.json"

    if not activities_file.exists():
        return "Noch nie geladen"

    try:
        data = json.loads(activities_file.read_text(encoding="utf-8"))
        last_updated = data.get("last_updated", "")
        if last_updated:
            dt = datetime.fromisoformat(last_updated)
            return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        pass

    return "Unbekannt"


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

    combined_output = "\n\n".join(output_parts) if output_parts else "Garmin-Daten wurden neu geladen."
    return result.returncode == 0, combined_output


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
    st.session_state.setdefault("coach_status_lines", ["Bereit."])
    st.session_state.setdefault("coach_status_level", "info")
    st.session_state.setdefault("discord_verified", bool(profile.get("discord_user_id", "").strip()))


def _set_coach_status(lines: list[str], level: str = "info") -> None:
    st.session_state.coach_status_lines = lines
    st.session_state.coach_status_level = level


def _render_coach_status(container: Any) -> None:
    lines = st.session_state.get("coach_status_lines", [])
    level = st.session_state.get("coach_status_level", "info")
    message = "\n".join(lines) if lines else "Bereit."

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
        st.markdown("### Zugang & Profil")
        st.selectbox("Mobilität", MOBILITY_OPTIONS, key="mobility_config", help="Wähle den Mobilitätstyp, der deine Trainingsauswahl steuert.")
        st.selectbox("Trainingsziel", GOAL_OPTIONS, key="goal_config", help="Das Ziel wird zur Auswahl der passenden Einheit verwendet.")
        st.text_area(
            "Sonstig zu berücksichtigende Aspekte",
            key="preference_config",
            height=96,
            placeholder="z. B. ich trage gerne sonnenbrille, keine harten Sprints, lieber morgens trainieren",
            help="Zusätzliche Hinweise, die der Coach bei der Empfehlung berücksichtigen soll.",
        )
        st.markdown("---")
        st.markdown("### Coach")
        reload_clicked = st.button("Garmin-Daten neu laden", use_container_width=True)
        refresh_clicked = st.button("Empfehlung neu laden (KI)", use_container_width=True)
        status_box = st.empty()
        _render_coach_status(status_box)

        if reload_clicked:
            _set_coach_status(["Garmin-Daten werden neu geladen..."], "info")
            with st.spinner("Garmin-Daten werden neu geladen..."):
                success, message = _reload_garmin_data(user_id)
            if success:
                st.success("Garmin-Daten wurden aktualisiert.")
                st.info(f"Neu geladen: {_get_last_fetch_timestamp()}")
                _set_coach_status(["Garmin-Daten wurden aktualisiert.", "KI wird neu konsultiert..."], "info")
            else:
                st.error("Garmin-Daten konnten nicht neu geladen werden.")
                _set_coach_status(["Fehler beim Garmin-Reload.", message], "error")
            with st.expander("Reload-Ausgabe", expanded=not success):
                st.code(message, language="text")
            st.session_state.refresh_recommendation = True
            st.session_state.trigger_notification_on_refresh = True
            st.rerun()

        if refresh_clicked:
            st.session_state.refresh_recommendation = True
            st.session_state.trigger_notification_on_refresh = True
            _set_coach_status(["KI wird gefragt..."], "info")
            st.rerun()

        st.markdown("---")
        st.markdown("### Konten & Benachrichtigungen")
        st.markdown("#### Discord")
        discord_already_linked = bool(str(st.session_state.get("discord_user_id_config", "")).strip())
        if registered_via_email:
            if discord_already_linked:
                st.toggle("Discord DM senden", key="notify_discord_config")
                st.text_input("Discord User-ID", key="discord_user_id_config", help="Empfaenger-ID fuer Discord DM via Bot-Token.", disabled=True)
                st.caption("Discord ist bereits verknüpft.")
            else:
                st.text_input("Discord User-ID zum Verknüpfen", key="link_discord_target_config", help="An diese Discord-ID wird ein 6-stelliger Link-Code gesendet.")
                if st.button("Code an Discord senden", use_container_width=True, key="send_link_discord_code_btn"):
                    target_discord_id = str(st.session_state.link_discord_target_config).strip()
                    if not target_discord_id:
                        st.error("Bitte eine Discord User-ID eingeben.")
                    else:
                        link_user = user_management.request_contact_link(user_id, "discord", target_discord_id)
                        code = str(link_user.get("pending_link", {}).get("verification_code", "")).strip()
                        if not code:
                            st.error("Konnte keinen Link-Code erzeugen.")
                        else:
                            sent, msg = send_verification_dm(target_discord_id, code)
                            if sent:
                                st.success("Link-Code per Discord-DM gesendet.")
                            else:
                                st.error(f"Fehler beim Discord-Versand: {msg}")
                st.text_input("Link-Code Discord", key="link_discord_code_config", help="6-stelligen Code aus Discord eingeben.")
                if st.button("Discord verknüpfen", use_container_width=True, key="verify_link_discord_code_btn"):
                    target_discord_id = str(st.session_state.link_discord_target_config).strip()
                    code = str(st.session_state.link_discord_code_config).strip()
                    if not target_discord_id:
                        st.error("Bitte zuerst die Discord User-ID angeben.")
                    elif not code:
                        st.error("Bitte den Link-Code eingeben.")
                    else:
                        ok = user_management.verify_contact_link(user_id, "discord", target_discord_id, code)
                        if ok:
                            profile = load_user_profile(user_id=user_id) or {}
                            profile["discord_user_id"] = target_discord_id
                            profile["notify_discord"] = True
                            save_user_profile(profile, user_id=user_id)
                            st.session_state.discord_user_id_config = target_discord_id
                            st.session_state.notify_discord_config = True
                            st.success("Discord erfolgreich verknüpft.")
                        else:
                            st.error("Link-Code ist ungültig oder abgelaufen.")
        else:
            st.toggle("Discord DM senden", key="notify_discord_config")
            st.text_input("Discord User-ID", key="discord_user_id_config", help="Empfaenger-ID fuer Discord DM via Bot-Token.")
            st.caption("Mit Discord registriert.")

        st.markdown("#### Email")
        email_already_linked = bool(str(st.session_state.get("email_config", "")).strip())
        if registered_via_email:
            st.toggle("Email-Benachrichtigung senden", key="notify_email_config")
            st.text_input("Email-Adresse", key="email_config", help="Email-Adresse für tägliche Trainingsempfehlungen mit HTML-Formatierung.", disabled=email_already_linked)
            st.caption("Mit Email registriert.")
        else:
            if email_already_linked:
                st.toggle("Email-Benachrichtigung senden", key="notify_email_config")
                st.text_input("Email-Adresse", key="email_config", help="Email-Adresse für tägliche Trainingsempfehlungen mit HTML-Formatierung.")
                st.caption("Email ist bereits verknüpft.")
            else:
                st.text_input("Email zum Verknüpfen", key="link_email_target_config", help="An diese Adresse wird ein 6-stelliger Link-Code gesendet.")
                if st.button("Code an Email senden", use_container_width=True, key="send_link_email_code_btn"):
                    target_email = str(st.session_state.link_email_target_config).strip().lower()
                    if not target_email:
                        st.error("Bitte eine Email-Adresse eingeben.")
                    else:
                        link_user = user_management.request_contact_link(user_id, "email", target_email)
                        code = str(link_user.get("pending_link", {}).get("verification_code", "")).strip()
                        if not code:
                            st.error("Konnte keinen Link-Code erzeugen.")
                        else:
                            subject = "Dein Link-Code für PersonalGarminAICoach"
                            text = f"Dein Link-Code für die Verknüpfung mit deinem Account ist: {code}\n\nGib diesen Code in der App ein, um die Email für Benachrichtigungen zu verknüpfen."
                            html = f"<p>Dein Link-Code für die Verknüpfung mit deinem Account ist: <strong>{code}</strong></p>"
                            sent, msg = send_email(subject=subject, body_text=text, body_html=html, recipient_email=target_email)
                            if sent:
                                st.success("Link-Code per Email gesendet.")
                            else:
                                st.error(f"Fehler beim Email-Versand: {msg}")
                st.text_input("Link-Code Email", key="link_email_code_config", help="6-stelligen Code aus der Email eingeben.")
                if st.button("Email verknüpfen", use_container_width=True, key="verify_link_email_code_btn"):
                    target_email = str(st.session_state.link_email_target_config).strip().lower()
                    code = str(st.session_state.link_email_code_config).strip()
                    if not target_email:
                        st.error("Bitte zuerst die Email-Adresse angeben.")
                    elif not code:
                        st.error("Bitte den Link-Code eingeben.")
                    else:
                        ok = user_management.verify_contact_link(user_id, "email", target_email, code)
                        if ok:
                            profile = load_user_profile(user_id=user_id) or {}
                            profile["email"] = target_email
                            profile["notify_email"] = True
                            save_user_profile(profile, user_id=user_id)
                            st.session_state.email_config = target_email
                            st.session_state.notify_email_config = True
                            st.success("Email erfolgreich verknüpft.")
                        else:
                            st.error("Link-Code ist ungültig oder abgelaufen.")

        st.markdown("---")
        save_clicked = st.button("Profil speichern", use_container_width=True)
        if save_clicked:
            _save_profile_from_sidebar(user_id=user_id)
            st.success("Profil gespeichert")

        logout_clicked = st.button("Logout", use_container_width=True)
        if logout_clicked:
            st.session_state.discord_verified = False
            st.query_params.pop("auth", None)
            st.session_state.pop("active_discord_id", None)
            st.session_state.pop("temp_discord_id", None)
            st.session_state.pop("temp_code_input", None)
            st.session_state.pop("temp_code_sent", None)
            st.info("Du wurdest abgemeldet. Bitte registriere dich erneut.")
            st.rerun()

    return _save_profile_from_sidebar(user_id=user_id), status_box
