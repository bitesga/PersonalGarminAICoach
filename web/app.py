from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import streamlit as st

ROOT_DIR = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT_DIR / "core"
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
    "email_verified": False,
    "email": "",
    "mobility": "Läufer",
    "preference": "Trainiert gerne draußen",
    "goal": "Maximale Kraft und Ausdauer-Erhalt",
    "notify_discord": False,
    "discord_user_id": "",
    "notify_email_enabled": False,
}


def _to_number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None



def _latest_day(daily_stats: dict[str, Any]) -> dict[str, Any]:
    if not daily_stats:
        return {}
    latest_key = sorted(daily_stats.keys())[-1]
    latest = daily_stats.get(latest_key, {})
    return latest if isinstance(latest, dict) else {}



def _init_state() -> None:
    profile = load_user_profile()
    for key, default_value in DASHBOARD_DEFAULTS.items():
        st.session_state.setdefault(key, profile.get(key, default_value))
    if "refresh_recommendation" not in st.session_state:
        st.session_state.refresh_recommendation = False



def _save_profile_from_sidebar() -> dict[str, Any]:
    profile = {
        "email_verified": st.session_state.email_verified,
        "email": st.session_state.email.strip(),
        "mobility": st.session_state.mobility.strip(),
        "preference": st.session_state.preference.strip(),
        "goal": st.session_state.goal.strip(),
        "notify_discord": bool(st.session_state.notify_discord),
        "discord_user_id": st.session_state.discord_user_id.strip(),
        "notify_email_enabled": bool(st.session_state.notify_email_enabled),
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
    expected_code = os.getenv("STREAMLIT_VERIFICATION_CODE", "")
    if st.session_state.email_verified:
        return True

    st.markdown(
        "<div class='hero'><h1>Personal Garmin AI Coach</h1><p>Dein Fitness-Dashboard entsteht hier. Verifiziere zuerst deine E-Mail, dann bekommst du Zugriff auf Daten, Präferenzen und Empfehlungen.</p></div>",
        unsafe_allow_html=True,
    )
    st.write("")

    with st.form("verification_form", clear_on_submit=False):
        email = st.text_input("E-Mail", value=st.session_state.email or "", placeholder="name@example.com")
        verification_code = st.text_input(
            "Verifikationscode",
            type="password",
            help="Prototyp: Code aus `STREAMLIT_VERIFICATION_CODE` in der `.env`.",
        )
        submitted = st.form_submit_button("Zugang freischalten")

    if submitted:
        if not email:
            st.error("Bitte zuerst eine E-Mail eingeben.")
            return False
        if expected_code and verification_code != expected_code:
            st.error("Verifikationscode ist falsch.")
            return False

        st.session_state.email = email
        st.session_state.email_verified = True
        _save_profile_from_sidebar()
        st.success("E-Mail verifiziert. Dashboard wird freigeschaltet.")
        st.rerun()

    st.info("Im Prototyp ist die Verifikation lokal simuliert. Der echte E-Mail-Flow kann später an Backend oder Discord angebunden werden.")
    return False



def _render_sidebar() -> dict[str, Any]:
    with st.sidebar:
        st.markdown("### Zugang & Profil")
        st.text_input("E-Mail", key="email")
        st.checkbox("E-Mail verifiziert", key="email_verified")
        st.selectbox(
            "Mobilität",
            ["Gesund", "Rollstuhlfahrer", "Sonstige Einschränkungen"],
            key="mobility",
        )
        st.text_input("Zu berücksichtigende Aspekte", key="preference")
        st.text_input("Trainingsziel", key="goal")
        st.markdown("---")
        st.markdown("### Coach")
        reload_clicked = st.button("Garmin-Daten neu laden", use_container_width=True)
        refresh_clicked = st.button("Empfehlung neu berechnen", use_container_width=True)
        if reload_clicked:
            with st.spinner("Garmin-Daten werden neu geladen..."):
                success, message = _reload_garmin_data()
            if success:
                st.success("Garmin-Daten wurden aktualisiert.")
            else:
                st.error("Garmin-Daten konnten nicht neu geladen werden.")
            with st.expander("Reload-Ausgabe", expanded=not success):
                st.code(message, language="text")
            st.session_state.refresh_recommendation = True
            st.rerun()
        if refresh_clicked:
            st.session_state.refresh_recommendation = True

        st.markdown("---")
        st.markdown("### Benachrichtigung")
        st.toggle("Discord DM senden", key="notify_discord")
        st.text_input("Discord User-ID", key="discord_user_id", help="Empfaenger-ID fuer Discord DM via Bot-Token.")
        st.toggle("E-Mail senden", key="notify_email_enabled", help="Verwendet die verifizierte Account-E-Mail.")

        save_clicked = st.button("Profil speichern", use_container_width=True)
        if save_clicked:
            profile = _save_profile_from_sidebar()
            st.success("Profil gespeichert")
            return profile

    return _save_profile_from_sidebar()



def _render_summary_cards(daily_stats: dict[str, Any], activities: list[dict[str, Any]]) -> None:
    latest = _latest_day(daily_stats)
    sleep_score = _to_number(latest.get("sleep_score"))
    body_battery = _to_number(latest.get("body_battery"))
    stress = _to_number(latest.get("stress"))
    vo2_max = _to_number(latest.get("vo2_max"))
    resting_hr = _to_number(latest.get("resting_heart_rate"))

    cols = st.columns(5)
    metrics = [
        ("Sleep", sleep_score, "/100"),
        ("Body Battery", body_battery, "/100"),
        ("Stress", stress, "avg"),
        ("VO2Max", vo2_max, "ml/kg/min"),
        ("RHR", resting_hr, "bpm"),
    ]

    for column, (label, value, suffix) in zip(cols, metrics):
        with column:
            st.metric(label, "n/a" if value is None else round(value, 1), suffix)

    st.write("")



def _render_activities(activities: list[dict[str, Any]]) -> None:
    st.markdown("<h3 class='section-title'>Letzte Aktivitäten</h3>", unsafe_allow_html=True)
    if not activities:
        st.info("Keine Aktivitäten gefunden.")
        return

    rows = []
    for activity in activities:
        rows.append(
            {
                "Datum": activity.get("date", "n/a"),
                "Typ": activity.get("activity_type", "n/a"),
                "Primär": activity.get("primary_metric", "n/a"),
                "Dauer": activity.get("duration", "n/a"),
                "Kalorien": activity.get("calories", "n/a"),
                "Distanz": activity.get("distance", "n/a"),
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




def _render_dispatch_preview(recommendation: dict[str, Any]) -> None:
    title = recommendation.get("titel", "Coach Update")
    intensity = recommendation.get("intensitaet", "n/a")
    body = recommendation.get("empfehlung", "")
    source = recommendation.get("source", "model")
    message = (
        f"{title} | Intensität {intensity}/10 | Quelle {source}\n"
        f"{body}\n"
        f"Nutze das später als Discord- oder Streamlit-Nachricht."
    )

    with st.expander("Nachricht für Discord / Push / Streamlit", expanded=False):
        st.code(message, language="text")



def main() -> None:
    _init_state()

    daily_stats = load_daily_stats()
    activities = load_activities()

    if not _verification_gate():
        return

    profile = _render_sidebar()
    coach_profile = _build_profile()
    refresh = bool(st.session_state.pop("refresh_recommendation", False))

    recommendation = coach_agent.get_coach_recommendation(
        profile=coach_profile,
        daily_stats=daily_stats,
        activities=activities,
        refresh=refresh,
    )

    notify_result = notify_recommendation(recommendation, profile)
    if notify_result["sent"]:
        st.success(" | ".join(notify_result["sent"]))
    for error in notify_result["errors"]:
        st.error(error)

    st.markdown(
        "<div class='hero'><h1>Personal Garmin AI Coach</h1><p>Fitnessdaten, Aktivitäten und die heutige Empfehlung in einem Dashboard. Der Coach nutzt einen 6-Stunden-Cache, damit Token gespart werden.</p></div>",
        unsafe_allow_html=True,
    )
    st.write("")

    _render_summary_cards(daily_stats, activities)
    st.write("")

    _render_recommendation(recommendation)
    _render_activities(activities)

    st.write("")
    _render_dispatch_preview(recommendation)

    st.write("")
    st.markdown("<div class='card'><div class='small-label'>Technische Notiz</div><p>Der echte E-Mail-Flow, Discord-Versand und die Persistenz weiterer Nutzerzugriffe können später an ein Backend angebunden werden. Der aktuelle Prototyp zeigt bereits den vollen Datenfluss bis zur Empfehlung.</p></div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
