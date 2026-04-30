from __future__ import annotations

import json
import os
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
from core.data_persistence import (
    load_activities,
    load_daily_stats,
    load_user_profile,
    save_user_profile,
)
from core.notification_service import notify_recommendation
from datetime import datetime


st.set_page_config(
    page_title="Personal Garmin AI Coach",
    page_icon="🏁",
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
  padding-top: 1.4rem;
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
    "mobility": "Gesund",
    "preference": "",
    "goal": "Kraft und Ausdauer maximieren",
    "notify_discord": False,
    "discord_user_id": "",
}

MOBILITY_OPTIONS = ["Gesund", "Rollstuhl", "Leichte Einschränkungen"]
GOAL_OPTIONS = ["Kraft und Ausdauer maximieren", "Ausdauer Fokus", "Kraft Fokus"]


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
        # For strength training, primary_metric is exercise list
        return str(primary_metric)[:40] if primary_metric else "n/a"
    
    # For cardio/endurance, primary_metric is training effect score (Garmin scale)
    try:
        effect = float(primary_metric)
        effect_label = "Anaerob" if effect >= 5.0 else "Aerob"
        return f"{effect_label} ({effect:.2f})"
    except (TypeError, ValueError):
        return str(primary_metric)[:40] if primary_metric else "n/a"


def _get_last_fetch_timestamp() -> str:
    """Get the last updated timestamp from activities.json."""
    from pathlib import Path
    data_dir = Path(__file__).resolve().parents[1] / "data"
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



def _init_state() -> None:
    profile = load_user_profile()
    normalized_mobility = _normalize_choice(profile.get("mobility", DASHBOARD_DEFAULTS["mobility"]), MOBILITY_OPTIONS, DASHBOARD_DEFAULTS["mobility"])
    normalized_goal = _normalize_choice(profile.get("goal", DASHBOARD_DEFAULTS["goal"]), GOAL_OPTIONS, DASHBOARD_DEFAULTS["goal"])
    st.session_state["mobility"] = _normalize_choice(st.session_state.get("mobility", normalized_mobility), MOBILITY_OPTIONS, normalized_mobility)
    st.session_state["goal"] = _normalize_choice(st.session_state.get("goal", normalized_goal), GOAL_OPTIONS, normalized_goal)
    st.session_state.setdefault("preference", str(profile.get("preference", DASHBOARD_DEFAULTS["preference"])).strip())
    st.session_state.setdefault("notify_discord", bool(profile.get("notify_discord", DASHBOARD_DEFAULTS["notify_discord"])))
    st.session_state.setdefault("discord_user_id", str(profile.get("discord_user_id", DASHBOARD_DEFAULTS["discord_user_id"])).strip())
    if "refresh_recommendation" not in st.session_state:
        st.session_state.refresh_recommendation = False
    if "trigger_notification_on_refresh" not in st.session_state:
        st.session_state.trigger_notification_on_refresh = False
    st.session_state.setdefault("coach_status_lines", ["Bereit."])
    st.session_state.setdefault("coach_status_level", "info")


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



def _save_profile_from_sidebar() -> dict[str, Any]:
    profile = {
        "mobility": st.session_state.mobility.strip(),
        "preference": st.session_state.preference.strip(),
        "goal": st.session_state.goal.strip(),
        "notify_discord": bool(st.session_state.notify_discord),
        "discord_user_id": st.session_state.discord_user_id.strip(),
    }
    save_user_profile(profile)
    return profile


def _reload_garmin_data() -> tuple[bool, str]:
    script_path = CORE_DIR / "fetch_garmin_data.py"
    command = [sys.executable, str(script_path)]

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



def _build_profile() -> coach_agent.CoachProfile:
    return coach_agent.CoachProfile(
        mobility=st.session_state.mobility,
        preference=st.session_state.preference,
        goal=st.session_state.goal,
    )



def _verification_gate() -> bool:
    return True



def _render_sidebar() -> tuple[dict[str, Any], Any]:
    with st.sidebar:
        st.markdown("### Zugang & Profil")
        st.selectbox(
            "Mobilität",
            MOBILITY_OPTIONS,
            key="mobility",
            help="Wähle den Mobilitätstyp, der deine Trainingsauswahl steuert.",
        )
        st.selectbox(
            "Trainingsziel",
            GOAL_OPTIONS,
            key="goal",
            help="Das Ziel wird zur Auswahl der passenden Einheit verwendet.",
        )
        st.text_area(
            "Sonstig zu berücksichtigende Aspekte",
            key="preference",
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
                success, message = _reload_garmin_data()
            if success:
                st.success("Garmin-Daten wurden aktualisiert.")
                st.info(f"Neu geladen: {_get_last_fetch_timestamp()}")
                _set_coach_status(
                    [
                        "Garmin-Daten wurden aktualisiert.",
                        "KI wird neu konsultiert...",
                    ],
                    "info",
                )
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
        st.markdown("### Benachrichtigung")
        st.toggle("Discord DM senden", key="notify_discord")
        st.text_input("Discord User-ID", key="discord_user_id", help="Empfaenger-ID fuer Discord DM via Bot-Token.")
        st.caption("Discord Server für DM-Setup: https://discord.gg/DPMpqmEaN7")

        st.markdown("---")
        save_clicked = st.button("Profil speichern", use_container_width=True)
        if save_clicked:
            profile = _save_profile_from_sidebar()
            st.success("Profil gespeichert")
            return profile, status_box

    return _save_profile_from_sidebar(), status_box



def _render_summary_cards(daily_stats: dict[str, Any], activities: list[dict[str, Any]]) -> None:
    latest = _latest_day(daily_stats)
    sleep_score = _to_number(latest.get("sleep_score"))
    body_battery = _to_number(latest.get("body_battery"))
    stress = _to_number(latest.get("stress"))
    vo2_max = _to_number(latest.get("vo2_max"))
    resting_hr = _to_number(latest.get("resting_heart_rate"))

    cols = st.columns(5)
    metrics = [
        ("Sleep", sleep_score, "/100", f"Sleep Score der letzten Nacht: {sleep_score if sleep_score is not None else 'n/a'}"),
        ("Body Battery", body_battery, "/100", f"Energielevel fuer Training: {body_battery if body_battery is not None else 'n/a'}"),
        ("Stress", stress, "avg", f"Durchschnittlicher Stresswert des Tages: {stress if stress is not None else 'n/a'}"),
        ("VO2Max", vo2_max, "ml/kg/min", f"Aussage ueber aerobe Fitness: {vo2_max if vo2_max is not None else 'n/a'}"),
        ("RHR", resting_hr, "bpm", f"Ruhepuls: {resting_hr if resting_hr is not None else 'n/a'}"),
    ]

    for column, (label, value, suffix, help_text) in zip(cols, metrics):
        with column:
            st.metric(label, "n/a" if value is None else round(value, 1), suffix, help=help_text)

    st.write("")



def _render_activities(activities: list[dict[str, Any]]) -> None:
    st.markdown("<h3 class='section-title'>Letzte Aktivitäten</h3>", unsafe_allow_html=True)
    last_fetch = _get_last_fetch_timestamp()
    st.caption(f"Letzte Aktualisierung: {last_fetch}")
    
    if not activities:
        st.info("Keine Aktivitäten gefunden.")
        return

    rows = []
    for activity in activities:
        rows.append(
            {
                "Datum": activity.get("date", "n/a"),
                "Typ": activity.get("activity_type", "n/a"),
                "Trainingseffekt": _format_training_effect(
                    activity.get("activity_type", ""),
                    activity.get("primary_metric", "n/a")
                ),
                "Dauer": _format_duration(activity.get("duration", "n/a")),
                "Kalorien": f"{activity.get('calories', 'n/a'):.0f}" if isinstance(activity.get('calories'), (int, float)) else "n/a",
                "Distanz": _format_distance(activity.get("distance", "n/a")) if activity.get("distance") else "—",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

def _render_recommendation(recommendation: dict[str, Any]) -> None:
    st.markdown("<h3 class='section-title'>Empfehlung für das nächste Training</h3>", unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class='card reco-box'>
          <div class='reco-title'>{recommendation.get('titel', 'Empfehlung')}</div>
          <div class='reco-meta'>Intensität {recommendation.get('intensitaet', 'n/a')}/10 · Quelle {recommendation.get('source', 'model')}</div>
          <p><strong>Empfehlung:</strong> {recommendation.get('empfehlung', 'n/a')}</p>
          <p><strong>Begründung:</strong> {recommendation.get('begruendung', 'n/a')}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    
    st.write("")
    st.write("")


def main() -> None:
    _init_state()

    daily_stats = load_daily_stats()
    activities = load_activities()

    if not _verification_gate():
        return

    profile, status_box = _render_sidebar()
    coach_profile = _build_profile()
    refresh = bool(st.session_state.pop("refresh_recommendation", False))
    notify_on_refresh = bool(st.session_state.pop("trigger_notification_on_refresh", False))

    if refresh:
        _set_coach_status(["KI wird gefragt..."], "info")
        _render_coach_status(status_box)
        with st.spinner("KI wird neu konsultiert..."):
            recommendation = coach_agent.get_coach_recommendation(
                profile=coach_profile,
                daily_stats=daily_stats,
                activities=activities,
                refresh=True,
            )
        _set_coach_status(["KI-Antwort wird ins Dashboard geladen."], "info")
        _render_coach_status(status_box)
    else:
        recommendation = coach_agent.get_coach_recommendation(
            profile=coach_profile,
            daily_stats=daily_stats,
            activities=activities,
            refresh=False,
        )

    if notify_on_refresh:
        _set_coach_status(["Notification wird gesendet..."], "info")
        _render_coach_status(status_box)
        try:
            notify_result = notify_recommendation(recommendation, profile, daily_stats=daily_stats)
        except TypeError:
            notify_result = notify_recommendation(recommendation, profile)
        if notify_result["sent"]:
            st.success(" | ".join(notify_result["sent"]))
            _set_coach_status(["Gesendet: " + " | ".join(notify_result["sent"])], "success")
        for error in notify_result["errors"]:
            st.error(error)
            _set_coach_status(["Fehler: " + error], "error")

        if not notify_result["sent"] and not notify_result["errors"]:
            skipped = notify_result.get("skipped", [])
            if skipped:
                _set_coach_status(["Hinweis: " + skipped[0]], "info")

        if recommendation.get("source") == "local":
            reason = str(recommendation.get("fallback_reason", "LLM nicht erreichbar oder API-Key fehlt.")).strip()
            _set_coach_status([f"Lokaler Fallback aktiv: {reason}"], "error")

        _render_coach_status(status_box)
    elif refresh:
        if recommendation.get("source") == "local":
            reason = str(recommendation.get("fallback_reason", "LLM nicht erreichbar oder API-Key fehlt.")).strip()
            _set_coach_status([f"Lokaler Fallback aktiv: {reason}"], "error")
        else:
            _set_coach_status(["Aktualisierung abgeschlossen."], "success")
        _render_coach_status(status_box)

    st.markdown(
        "<div class='hero'><h1>Personal Garmin AI Coach</h1><p>Fitnessdaten, Aktivitäten und die heutige Empfehlung in einem Dashboard. Der Coach nutzt einen 6-Stunden-Cache, damit Token gespart werden.</p></div>",
        unsafe_allow_html=True,
    )
    st.write("")

    _render_summary_cards(daily_stats, activities)
    st.write("")

    _render_recommendation(recommendation)
    _render_activities(activities)


if __name__ == "__main__":
    main()
