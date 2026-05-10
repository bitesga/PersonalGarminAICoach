from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, time as time_type
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import streamlit as st

from core.data_persistence import load_user_profile, save_user_profile
from core.notification_service import send_email, send_verification_dm
from core import user_management
from web.i18n import tr

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
    "location_latitude": 50.1155,
    "location_longitude": 8.6842,
    "auto_recommendation_enabled": False,
    "auto_recommendation_times": ["09:00", "15:00"],
}

MOBILITY_OPTIONS = ["Healthy", "Wheelchair", "Minor limitations"]
GOAL_OPTIONS = ["Build Strength and Endurance", "Endurance Focus", "Strength Focus"]


def _mobility_label(value: str) -> str:
    labels = {
        "Healthy": tr("Healthy", "Gesund"),
        "Wheelchair": tr("Wheelchair", "Rollstuhl"),
        "Minor limitations": tr("Minor limitations", "Leichte Einschraenkungen"),
    }
    return labels.get(value, value)


def _goal_label(value: str) -> str:
    labels = {
        "Build Strength and Endurance": tr("Build Strength and Endurance", "Kraft und Ausdauer aufbauen"),
        "Endurance Focus": tr("Endurance Focus", "Ausdauer Fokus"),
        "Strength Focus": tr("Strength Focus", "Kraft Fokus"),
    }
    return labels.get(value, value)


def _normalize_choice(value: Any, options: list[str], default_value: str) -> str:
    candidate = str(value).strip()
    if candidate in options:
        return candidate
    lowered = candidate.lower()
    if options == MOBILITY_OPTIONS:
        if "gesund" in lowered or "healthy" in lowered:
            return "Healthy"
        if "wheelchair" in lowered:
            return "Wheelchair"
        if "limitation" in lowered:
            return "Minor limitations"
        return "Healthy"
    if options == GOAL_OPTIONS:
        if (
            "build strength and endurance" in lowered
            or "kraft und ausdauer" in lowered
            or "strength and endurance" in lowered
        ):
            return "Build Strength and Endurance"
        if "endurance" in lowered or "ausdauer" in lowered or "marathon" in lowered:
            return "Endurance Focus"
        if "strength" in lowered or "kraft" in lowered:
            return "Strength Focus"
    return default_value


def _parse_time_value(value: Any, default_value: str) -> time_type:
    if isinstance(value, time_type):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), "%H:%M").time()
        except ValueError:
            pass
    return datetime.strptime(default_value, "%H:%M").time()


def _search_city_candidates(city_name: str, language: str = "en") -> list[dict[str, Any]]:
    query = str(city_name).strip()
    if not query:
        return []

    params = urllib.parse.urlencode(
        {
            "name": query,
            "count": 10,
            "language": language,
            "format": "json",
        }
    )
    url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"

    request = urllib.request.Request(url, headers={"User-Agent": "PersonalGarminAICoach/1.0"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return []

    results = payload.get("results", []) if isinstance(payload, dict) else []
    return [item for item in results if isinstance(item, dict)]


def _format_city_option(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "Unknown")
    admin1 = str(item.get("admin1") or "").strip()
    country = str(item.get("country") or item.get("country_code") or "").strip()
    latitude = item.get("latitude")
    longitude = item.get("longitude")
    location_parts = [part for part in [admin1, country] if part]
    location = ", ".join(location_parts)
    if location:
        return f"{name} ({location}) [{latitude}, {longitude}]"
    return f"{name} [{latitude}, {longitude}]"


def _get_last_fetch_timestamp() -> str:
    data_dir = ROOT_DIR / "data"
    activities_file = data_dir / "activities.json"

    if not activities_file.exists():
        return tr("Never loaded", "Nie geladen")

    try:
        data = json.loads(activities_file.read_text(encoding="utf-8"))
        last_updated = data.get("last_updated", "")
        if last_updated:
            dt = datetime.fromisoformat(last_updated)
            return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        pass

    return tr("Unknown", "Unbekannt")


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
    if "using cache" in combined_output.lower() or "completed (using cache)" in combined_output.lower():
        combined_output = "[CACHE_USED]\n" + combined_output
    if "AUTH_ERROR:" in combined_output:
        combined_output = "[AUTH_ERROR]\n" + combined_output
    if "RATE_LIMIT:" in combined_output:
        combined_output = "[RATE_LIMIT]\n" + combined_output
    if "CAPTCHA_REQUIRED:" in combined_output:
        combined_output = "[CAPTCHA_REQUIRED]\n" + combined_output
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
    st.session_state.setdefault("location_latitude_config", float(profile.get("location_latitude", DASHBOARD_DEFAULTS["location_latitude"])))
    st.session_state.setdefault("location_longitude_config", float(profile.get("location_longitude", DASHBOARD_DEFAULTS["location_longitude"])))
    auto_times = profile.get("auto_recommendation_times", DASHBOARD_DEFAULTS["auto_recommendation_times"])
    time_1 = auto_times[0] if isinstance(auto_times, list) and auto_times else DASHBOARD_DEFAULTS["auto_recommendation_times"][0]
    time_2 = auto_times[1] if isinstance(auto_times, list) and len(auto_times) > 1 else DASHBOARD_DEFAULTS["auto_recommendation_times"][1]
    st.session_state.setdefault("auto_reco_enabled_config", bool(profile.get("auto_recommendation_enabled", DASHBOARD_DEFAULTS["auto_recommendation_enabled"])))
    st.session_state.setdefault("auto_reco_time_1_config", _parse_time_value(time_1, DASHBOARD_DEFAULTS["auto_recommendation_times"][0]))
    st.session_state.setdefault("auto_reco_time_2_config", _parse_time_value(time_2, DASHBOARD_DEFAULTS["auto_recommendation_times"][1]))
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
    st.session_state.setdefault("city_search_query", "")
    st.session_state.setdefault("city_search_results", [])
    st.session_state.setdefault("city_selected_index", 0)


def _set_coach_status(lines: list[str], level: str = "info") -> None:
    st.session_state.coach_status_lines = lines
    st.session_state.coach_status_level = level


def _render_coach_status(container: Any) -> None:
    lines = st.session_state.get("coach_status_lines", [])
    level = st.session_state.get("coach_status_level", "info")
    message = "\n".join(lines) if lines else tr("Ready.", "Bereit.")

    if level == "success":
        container.success(message)
    elif level == "error":
        container.error(message)
    else:
        container.info(message)


def _save_profile_from_sidebar(user_id: str) -> dict[str, Any]:
    profile = load_user_profile(user_id=user_id) or {}
    times: list[str] = []
    for value in [st.session_state.auto_reco_time_1_config, st.session_state.auto_reco_time_2_config]:
        if isinstance(value, time_type):
            times.append(value.strftime("%H:%M"))
        elif isinstance(value, str) and value.strip():
            times.append(value.strip())
    times = sorted(set(times)) or list(DASHBOARD_DEFAULTS["auto_recommendation_times"])
    profile.update({
        "mobility": st.session_state.mobility_config.strip(),
        "preference": st.session_state.preference_config.strip(),
        "goal": st.session_state.goal_config.strip(),
        "notify_discord": bool(st.session_state.notify_discord_config),
        "discord_user_id": st.session_state.discord_user_id_config.strip(),
        "notify_email": bool(st.session_state.notify_email_config),
        "email": st.session_state.email_config.strip(),
        "location_latitude": float(st.session_state.location_latitude_config),
        "location_longitude": float(st.session_state.location_longitude_config),
        "ui_language": str(st.session_state.get("ui_language", "en")).strip().lower(),
        "auto_recommendation_enabled": bool(st.session_state.auto_reco_enabled_config),
        "auto_recommendation_times": times,
    })
    save_user_profile(profile, user_id=user_id)
    return profile


def render_sidebar(user_id: str) -> tuple[dict[str, Any], Any]:
    profile = load_user_profile(user_id=user_id) or {}
    registered_via_email = str(user_id).startswith("email:")

    with st.sidebar:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=88)
        st.markdown(f"### {tr('Access & Profile', 'Zugang & Profil')}")
        st.selectbox(
            tr("Mobility", "Mobilitaet"),
            MOBILITY_OPTIONS,
            key="mobility_config",
            format_func=_mobility_label,
            help=tr("Choose the mobility profile that guides training selection.", "Waehle das Mobilitaetsprofil fuer die Trainingsempfehlung."),
        )
        st.selectbox(
            tr("Training goal", "Trainingsziel"),
            GOAL_OPTIONS,
            key="goal_config",
            format_func=_goal_label,
            help=tr("The goal is used to select the most suitable session.", "Das Ziel wird fuer die passende Session verwendet."),
        )
        st.text_area(
            tr("Other considerations", "Weitere Hinweise"),
            key="preference_config",
            height=96,
            placeholder=tr("e.g., no hard sprints, prefer mornings, outdoor only", "z.B. keine harten Sprints, lieber morgens, nur draussen"),
            help=tr("Extra notes the coach should consider.", "Zusatzhinweise fuer den Coach."),
        )
        st.markdown(f"#### {tr('Location', 'Standort')}")
        st.text_input(
            tr("City", "Stadt"),
            key="city_search_query",
            placeholder=tr("e.g. Frankfurt", "z.B. Frankfurt"),
            help=tr("Search your city and auto-fill coordinates.", "Suche deine Stadt und setze die Koordinaten automatisch."),
        )
        search_city_clicked = st.button(tr("Search city", "Stadt suchen"), width="stretch", key="search_city_btn")
        if search_city_clicked:
            language = str(st.session_state.get("ui_language", "en")).strip().lower()
            search_lang = "de" if language == "de" else "en"
            results = _search_city_candidates(st.session_state.city_search_query, language=search_lang)
            st.session_state.city_search_results = results
            st.session_state.city_selected_index = 0
            if results:
                st.success(tr("City results loaded.", "Stadtergebnisse geladen."))
            else:
                st.warning(tr("No matching city found.", "Keine passende Stadt gefunden."))

        city_results = st.session_state.get("city_search_results", [])
        if city_results:
            options = [_format_city_option(item) for item in city_results]
            selected_label = st.selectbox(
                tr("Select city", "Stadt auswaehlen"),
                options,
                key="city_selection_label",
            )
            selected_index = options.index(selected_label)
            selected_city = city_results[selected_index]
            if st.button(tr("Use selected city", "Ausgewaehlte Stadt uebernehmen"), width="stretch", key="apply_city_btn"):
                try:
                    st.session_state.location_latitude_config = float(selected_city.get("latitude"))
                    st.session_state.location_longitude_config = float(selected_city.get("longitude"))
                    st.success(tr("Coordinates updated from city.", "Koordinaten aus Stadt uebernommen."))
                except (TypeError, ValueError):
                    st.error(tr("Could not apply coordinates for this city.", "Koordinaten fuer diese Stadt konnten nicht uebernommen werden."))

        st.number_input(
            tr("Latitude", "Breitengrad"),
            key="location_latitude_config",
            min_value=-90.0,
            max_value=90.0,
            step=0.0001,
            format="%.4f",
            help=tr("Used for weather-aware recommendations.", "Wird fuer wetterbasierte Empfehlungen genutzt."),
        )
        st.number_input(
            tr("Longitude", "Laengengrad"),
            key="location_longitude_config",
            min_value=-180.0,
            max_value=180.0,
            step=0.0001,
            format="%.4f",
            help=tr("Used for weather-aware recommendations.", "Wird fuer wetterbasierte Empfehlungen genutzt."),
        )
        st.markdown("---")
        st.markdown(f"### {tr('Coach', 'Coach')}")
        reload_clicked = st.button(tr("Refresh Garmin data", "Garmin-Daten aktualisieren"), width="stretch")
        refresh_clicked = st.button(tr("Refresh recommendation (AI)", "Empfehlung aktualisieren (KI)"), width="stretch")
        status_box = st.empty()
        _render_coach_status(status_box)

        config_warnings = _get_config_warnings()
        if config_warnings:
            st.warning("\n".join(config_warnings))

        if reload_clicked:
            _set_coach_status([tr("Refreshing Garmin data...", "Garmin-Daten werden aktualisiert...")], "info")
            with st.spinner(tr("Refreshing Garmin data...", "Garmin-Daten werden aktualisiert...")):
                success, message = _reload_garmin_data(user_id)
            auth_error = message.startswith("[AUTH_ERROR]")
            rate_limit_error = message.startswith("[RATE_LIMIT]")
            captcha_error = message.startswith("[CAPTCHA_REQUIRED]")
            if success:
                cache_used = message.startswith("[CACHE_USED]")
                if cache_used:
                    message = message.removeprefix("[CACHE_USED]\n")
                    st.info(tr("Garmin data updated from cached Garmin data.", "Garmin-Daten aus dem Cache aktualisiert."))
                else:
                    st.success(tr("Garmin data updated.", "Garmin-Daten aktualisiert."))
                st.info(f"{tr('Last refresh', 'Letzte Aktualisierung')}: {_get_last_fetch_timestamp()}")
                _set_coach_status([
                    tr("Garmin data updated from cached Garmin data.", "Garmin-Daten aus dem Cache aktualisiert.")
                    if cache_used
                    else tr("Garmin data updated.", "Garmin-Daten aktualisiert.")
                ], "success")
                st.session_state.garmin_data_updated = True
                _log_event("info", f"Garmin refresh succeeded for user {user_id}.")
            else:
                if auth_error:
                    message = message.removeprefix("[AUTH_ERROR]\n")
                    st.error(tr("Garmin login failed. Please check your email and password.", "Garmin-Login fehlgeschlagen. Bitte E-Mail und Passwort pruefen."))
                    _set_coach_status([
                        tr("Garmin login failed.", "Garmin-Login fehlgeschlagen."),
                        tr("Please check your email and password.", "Bitte E-Mail und Passwort pruefen."),
                    ], "error")
                    _log_event("error", f"Garmin auth failed for user {user_id}: {message}")
                elif rate_limit_error:
                    message = message.removeprefix("[RATE_LIMIT]\n")
                    st.warning(tr("Garmin is rate limiting the server. Using cached data if available.", "Garmin limitiert den Server. Falls verfuegbar, werden Cache-Daten genutzt."))
                    _set_coach_status([tr("Garmin rate limit detected.", "Garmin Rate-Limit erkannt."), message], "warning")
                    _log_event("warning", f"Garmin rate limit for user {user_id}: {message}")
                elif captcha_error:
                    message = message.removeprefix("[CAPTCHA_REQUIRED]\n")
                    st.error(tr("Garmin requires CAPTCHA approval. Cached data may be used instead.", "Garmin verlangt eine CAPTCHA-Freigabe. Stattdessen koennen Cache-Daten genutzt werden."))
                    _set_coach_status([tr("Garmin CAPTCHA required.", "Garmin CAPTCHA erforderlich."), message], "error")
                    _log_event("error", f"Garmin captcha required for user {user_id}: {message}")
                else:
                    st.error(tr("Garmin data could not be refreshed.", "Garmin-Daten konnten nicht aktualisiert werden."))
                    _set_coach_status([tr("Garmin refresh failed.", "Garmin-Aktualisierung fehlgeschlagen."), message], "error")
                    _log_event("error", f"Garmin refresh failed for user {user_id}: {message}")
            with st.expander(tr("Reload output", "Ausgabe aktualisieren"), expanded=False):
                st.code(message, language="text")
            st.rerun()

        if st.session_state.get("garmin_data_updated"):
            st.info(tr("Data updated. Load a new recommendation for this data?", "Daten aktualisiert. Neue Empfehlung fuer diese Daten laden?"))
            if st.button(tr("Load new recommendation", "Neue Empfehlung laden"), width="stretch", key="refresh_after_reload"):
                st.session_state.garmin_data_updated = False
                st.session_state.refresh_recommendation = True
                st.session_state.trigger_notification_on_refresh = True
                _set_coach_status([tr("Querying AI...", "KI wird abgefragt...")], "info")
                _log_event("info", f"Recommendation requested after Garmin refresh for user {user_id}.")
                st.rerun()
            if st.button(tr("Not now", "Nicht jetzt"), width="stretch", key="skip_refresh_after_reload"):
                st.session_state.garmin_data_updated = False
                _set_coach_status([tr("Ready.", "Bereit.")], "info")
                _log_event("info", f"Recommendation skipped after Garmin refresh for user {user_id}.")
                st.rerun()

        if refresh_clicked:
            st.session_state.refresh_recommendation = True
            st.session_state.trigger_notification_on_refresh = True
            _set_coach_status([tr("Querying AI...", "KI wird abgefragt...")], "info")
            _log_event("info", f"Manual recommendation refresh requested for user {user_id}.")
            st.rerun()

        st.markdown("---")
        st.markdown(f"### {tr('Automatic Recommendations', 'Automatische Empfehlungen')}")
        st.toggle(
            tr("Enable automatic recommendations", "Automatische Empfehlungen aktivieren"),
            key="auto_reco_enabled_config",
            help=tr("Fetch Garmin data and send a new recommendation at the selected times.", "Garmin-Daten abrufen und zu den gewaehlten Zeiten eine neue Empfehlung senden."),
        )
        auto_enabled = bool(st.session_state.auto_reco_enabled_config)
        time_col_1, time_col_2 = st.columns(2)
        with time_col_1:
            st.time_input(
                tr("Time 1", "Zeit 1"),
                key="auto_reco_time_1_config",
                disabled=not auto_enabled,
                help=tr("Use 24-hour format; server local time.", "24h-Format; lokale Serverzeit."),
            )
        with time_col_2:
            st.time_input(
                tr("Time 2", "Zeit 2"),
                key="auto_reco_time_2_config",
                disabled=not auto_enabled,
                help=tr("Use 24-hour format; server local time.", "24h-Format; lokale Serverzeit."),
            )
        st.caption(tr("Automatic recommendations use the server's local time.", "Automatische Empfehlungen nutzen die lokale Serverzeit."))

        st.markdown("---")
        st.markdown(f"### {tr('Accounts & Notifications', 'Konten & Benachrichtigungen')}")
        st.markdown(f"#### {tr('Discord', 'Discord')}")
        discord_already_linked = bool(str(st.session_state.get("discord_user_id_config", "")).strip())
        if registered_via_email:
            if discord_already_linked:
                st.toggle(tr("Send Discord DM", "Discord-DM senden"), key="notify_discord_config")
                st.text_input(tr("Discord user ID", "Discord-Nutzer-ID"), key="discord_user_id_config", help=tr("Recipient ID for Discord DMs via bot token.", "Empfaenger-ID fuer Discord-DMs ueber den Bot-Token."), disabled=True)
                st.caption(tr("Discord is already linked.", "Discord ist bereits verknuepft."))
            else:
                st.text_input(tr("Discord user ID to link", "Discord-Nutzer-ID zum Verknuepfen"), key="link_discord_target_config", help=tr("A 6-digit link code will be sent to this Discord ID.", "Ein 6-stelliger Link-Code wird an diese Discord-ID gesendet."))
                if st.button(tr("Send code to Discord", "Code an Discord senden"), width="stretch", key="send_link_discord_code_btn"):
                    target_discord_id = str(st.session_state.link_discord_target_config).strip()
                    if not target_discord_id:
                        st.error(tr("Please enter a Discord user ID.", "Bitte gib eine Discord-Nutzer-ID ein."))
                    else:
                        link_user = user_management.request_contact_link(user_id, "discord", target_discord_id)
                        code = str(link_user.get("pending_link", {}).get("verification_code", "")).strip()
                        if not code:
                            st.error(tr("Could not generate a link code.", "Link-Code konnte nicht erstellt werden."))
                        else:
                            sent, msg = send_verification_dm(target_discord_id, code)
                            if sent:
                                st.success(tr("Link code sent via Discord DM.", "Link-Code per Discord-DM gesendet."))
                            else:
                                st.error(f"{tr('Discord send failed', 'Discord-Senden fehlgeschlagen')}: {msg}")
                st.text_input(tr("Discord link code", "Discord-Link-Code"), key="link_discord_code_config", help=tr("Enter the 6-digit code from Discord.", "Gib den 6-stelligen Code aus Discord ein."))
                if st.button(tr("Link Discord", "Discord verknuepfen"), width="stretch", key="verify_link_discord_code_btn"):
                    target_discord_id = str(st.session_state.link_discord_target_config).strip()
                    code = str(st.session_state.link_discord_code_config).strip()
                    if not target_discord_id:
                        st.error(tr("Please enter the Discord user ID first.", "Bitte zuerst die Discord-Nutzer-ID eingeben."))
                    elif not code:
                        st.error(tr("Please enter the link code.", "Bitte den Link-Code eingeben."))
                    else:
                        ok = user_management.verify_contact_link(user_id, "discord", target_discord_id, code)
                        if ok:
                            profile = load_user_profile(user_id=user_id) or {}
                            profile["discord_user_id"] = target_discord_id
                            profile["notify_discord"] = True
                            save_user_profile(profile, user_id=user_id)
                            st.session_state.discord_user_id_config = target_discord_id
                            st.session_state.notify_discord_config = True
                            st.success(tr("Discord linked successfully.", "Discord erfolgreich verknuepft."))
                        else:
                            st.error(tr("Link code is invalid or expired.", "Link-Code ist ungueltig oder abgelaufen."))
        else:
            st.toggle(tr("Send Discord DM", "Discord-DM senden"), key="notify_discord_config")
            st.text_input(tr("Discord user ID", "Discord-Nutzer-ID"), key="discord_user_id_config", help=tr("Recipient ID for Discord DMs via bot token.", "Empfaenger-ID fuer Discord-DMs ueber den Bot-Token."))
            st.caption(tr("Registered with Discord.", "Mit Discord registriert."))

        st.markdown(f"#### {tr('Email', 'E-Mail')}")
        email_already_linked = bool(str(st.session_state.get("email_config", "")).strip())
        if registered_via_email:
            st.toggle(tr("Send email notifications", "E-Mail-Benachrichtigungen senden"), key="notify_email_config")
            st.text_input(tr("Email address", "E-Mail-Adresse"), key="email_config", help=tr("Email address for daily recommendations with HTML formatting.", "E-Mail-Adresse fuer taegliche Empfehlungen mit HTML-Formatierung."), disabled=email_already_linked)
            st.caption(tr("Registered with email.", "Mit E-Mail registriert."))
        else:
            if email_already_linked:
                st.toggle(tr("Send email notifications", "E-Mail-Benachrichtigungen senden"), key="notify_email_config")
                st.text_input(tr("Email address", "E-Mail-Adresse"), key="email_config", help=tr("Email address for daily recommendations with HTML formatting.", "E-Mail-Adresse fuer taegliche Empfehlungen mit HTML-Formatierung."))
                st.caption(tr("Email is already linked.", "E-Mail ist bereits verknuepft."))
            else:
                st.text_input(tr("Email to link", "E-Mail zum Verknuepfen"), key="link_email_target_config", help=tr("A 6-digit link code will be sent to this address.", "Ein 6-stelliger Link-Code wird an diese Adresse gesendet."))
                if st.button(tr("Send code to email", "Code per E-Mail senden"), width="stretch", key="send_link_email_code_btn"):
                    target_email = str(st.session_state.link_email_target_config).strip().lower()
                    if not target_email:
                        st.error(tr("Please enter an email address.", "Bitte gib eine E-Mail-Adresse ein."))
                    else:
                        link_user = user_management.request_contact_link(user_id, "email", target_email)
                        code = str(link_user.get("pending_link", {}).get("verification_code", "")).strip()
                        if not code:
                            st.error(tr("Could not generate a link code.", "Link-Code konnte nicht erstellt werden."))
                        else:
                            subject = "Your link code for PersonalGarminAICoach"
                            text = f"Your link code for connecting your account is: {code}\n\nEnter this code in the app to link your email for notifications."
                            html = f"<p>Your link code for connecting your account is: <strong>{code}</strong></p>"
                            sent, msg = send_email(subject=subject, body_text=text, body_html=html, recipient_email=target_email)
                            if sent:
                                st.success(tr("Link code sent via email.", "Link-Code per E-Mail gesendet."))
                            else:
                                st.error(f"{tr('Email send failed', 'E-Mail-Senden fehlgeschlagen')}: {msg}")
                st.text_input(tr("Email link code", "E-Mail-Link-Code"), key="link_email_code_config", help=tr("Enter the 6-digit code from the email.", "Gib den 6-stelligen Code aus der E-Mail ein."))
                if st.button(tr("Link email", "E-Mail verknuepfen"), width="stretch", key="verify_link_email_code_btn"):
                    target_email = str(st.session_state.link_email_target_config).strip().lower()
                    code = str(st.session_state.link_email_code_config).strip()
                    if not target_email:
                        st.error(tr("Please enter the email address first.", "Bitte zuerst die E-Mail-Adresse eingeben."))
                    elif not code:
                        st.error(tr("Please enter the link code.", "Bitte den Link-Code eingeben."))
                    else:
                        ok = user_management.verify_contact_link(user_id, "email", target_email, code)
                        if ok:
                            profile = load_user_profile(user_id=user_id) or {}
                            profile["email"] = target_email
                            profile["notify_email"] = True
                            save_user_profile(profile, user_id=user_id)
                            st.session_state.email_config = target_email
                            st.session_state.notify_email_config = True
                            st.success(tr("Email linked successfully.", "E-Mail erfolgreich verknuepft."))
                        else:
                            st.error(tr("Link code is invalid or expired.", "Link-Code ist ungueltig oder abgelaufen."))

        st.markdown("---")
        save_clicked = st.button(tr("Save profile", "Profil speichern"), width="stretch")
        if save_clicked:
            _save_profile_from_sidebar(user_id=user_id)
            st.success(tr("Profile saved", "Profil gespeichert"))

        logout_clicked = st.button(tr("Log out", "Abmelden"), width="stretch")
        if logout_clicked:
            st.session_state.discord_verified = False
            st.query_params.pop("auth", None)
            st.session_state.pop("active_discord_id", None)
            st.session_state.pop("temp_discord_id", None)
            st.session_state.pop("temp_code_input", None)
            st.session_state.pop("temp_code_sent", None)
            st.info(tr("You have been logged out. Please register again.", "Du wurdest abgemeldet. Bitte erneut anmelden."))
            st.rerun()

    return _save_profile_from_sidebar(user_id=user_id), status_box
