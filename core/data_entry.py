"""Data entry and Garmin login for manual data input."""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    import streamlit as st
except ImportError:
    st = None


def _tr(english: str, german: str) -> str:
    if st is None:
        return english
    language = str(st.session_state.get("ui_language", "en")).strip().lower()
    return german if language == "de" else english


def render_garmin_credentials_section() -> dict[str, str] | None:
    """Render a Garmin login form with email and password.

    Returns a dict with email/password when the button is clicked, otherwise None.
    """
    if st is None:
        return None

    st.markdown(f"### {_tr('Garmin Connect', 'Garmin Connect')}")
    st.write(
        _tr(
            "Sign in with your Garmin account to sync activities and fitness data automatically.",
            "Melde dich mit deinem Garmin-Konto an, um Aktivitaeten und Fitnessdaten automatisch zu synchronisieren.",
        )
    )

    col1, col2 = st.columns(2)
    with col1:
        email = st.text_input(
            _tr("Garmin email", "Garmin E-Mail"),
            placeholder="your.name@example.com",
            key="garmin_email",
            help=_tr("Email address for your Garmin account", "E-Mail-Adresse fuer dein Garmin-Konto"),
        )

    with col2:
        password = st.text_input(
            _tr("Garmin password", "Garmin Passwort"),
            type="password",
            placeholder="••••••••",
            key="garmin_password",
            help=_tr("Password for your Garmin account", "Passwort fuer dein Garmin-Konto"),
        )

    if st.button(_tr("✓ Connect Garmin account", "✓ Garmin-Konto verbinden"), key="connect_garmin_btn", width="stretch"):
        if email and password:
            st.success(_tr("Garmin account prepared successfully.", "Garmin-Konto erfolgreich vorbereitet."))
            return {"email": email.strip(), "password": password}
        st.error(_tr("Please enter both email and password.", "Bitte E-Mail und Passwort eingeben."))
        return None

    st.markdown(
        f"<span style='color:#94a3b8; font-size:0.85rem'>{_tr('Note: The login data is stored locally for the active Discord user.', 'Hinweis: Die Login-Daten werden lokal fuer den aktiven Discord-Nutzer gespeichert.')}</span>",
        unsafe_allow_html=True,
    )
    return None


def render_manual_health_entry() -> dict[str, Any]:
    """Render a form for manual health metric entry (sleep, body_battery, stress, etc.)."""
    if st is None:
        return {}

    st.markdown(f"### {_tr('Enter manual health data', 'Manuelle Gesundheitsdaten eingeben')}")

    training_balance_options = [
        "N/A",
        "AEROBIC_HIGH_SHORTAGE",
        "AEROBIC_LOW_SHORTAGE",
        "AEROBIC_BALANCED",
        "ANAEROBIC_HIGH_SHORTAGE",
        "ANAEROBIC_LOW_SHORTAGE",
        "ANAEROBIC_BALANCED",
        "RECOVERY",
        "OVERREACHING",
        "OTHER",
    ]
    
    col1, col2 = st.columns(2)
    with col1:
        sleep_score = st.slider(_tr("Sleep Score (0-100)", "Schlaf-Score (0-100)"), 0, 100, 75, key="manual_sleep_score")
        body_battery = st.slider(_tr("Body Battery (0-100)", "Koerperbatterie (0-100)"), 0, 100, 70, key="manual_body_battery")
        stress = st.slider(_tr("Stress (0-100)", "Stress (0-100)"), 0, 100, 20, key="manual_stress")
        vo2_max = st.slider("VO2Max (ml/kg/min)", 20.0, 110.0, 45.0, step=0.1, key="manual_vo2_max")
        resting_hr = st.slider(_tr("Resting Heart Rate (bpm)", "Ruhepuls (bpm)"), 0, 100, 60, step=1, key="manual_resting_hr")
    
    with col2:
        training_load_acute = st.number_input(
            _tr("Acute Training Load", "Akute Trainingsbelastung"),
            0.0,
            1000.0,
            0.0,
            step=1.0,
            key="manual_training_load_acute",
        )
        training_balance_feedback = st.selectbox(
            _tr("Training balance", "Trainingsbalance"),
            training_balance_options,
            index=0,
            key="manual_training_balance_feedback",
        )
        if training_balance_feedback == "OTHER":
            training_balance_feedback = st.text_input(
                _tr("Training balance (custom)", "Trainingsbalance (benutzerdefiniert)"),
                placeholder=_tr("e.g. AEROBIC_HIGH_SHORTAGE", "z.B. AEROBIC_HIGH_SHORTAGE"),
                key="manual_training_balance_feedback_other",
            ).strip() or "N/A"
        date_input = st.date_input(_tr("Date", "Datum"), value=datetime.now(), key="manual_date")
        time_input = st.time_input(_tr("Time", "Uhrzeit"), value=datetime.now().time(), key="manual_time")
    
    return {
        "date": str(date_input),
        "sleep_score": sleep_score,
        "body_battery": body_battery,
        "stress": stress,
        "vo2_max": vo2_max,
        "resting_heart_rate": resting_hr,
        "training_load_acute": training_load_acute,
        "training_balance_feedback": training_balance_feedback,
        "time": time_input.isoformat(timespec="seconds"),
    }


def render_manual_activity_entry() -> dict[str, Any] | None:
    """Render a form for manual activity entry."""
    if st is None:
        return None

    st.markdown(f"### {_tr('Add manual activity', 'Manuelle Aktivitaet hinzufuegen')}")
    
    col1, col2 = st.columns(2)
    with col1:
        activity_type = st.selectbox(
            _tr("Activity type", "Aktivitaetstyp"),
            ["running", "cycling", "strength_training", "swimming", "walking", "other"],
            key="manual_activity_type"
        )
        duration_minutes = st.number_input(_tr("Duration (minutes)", "Dauer (Minuten)"), 1, 300, 45, step=5, key="manual_duration")
        distance_km = st.number_input(_tr("Distance (km, 0 if unknown)", "Distanz (km, 0 wenn unbekannt)"), 0.0, 100.0, 0.0, step=0.1, key="manual_distance")
    
    with col2:
        calories = st.number_input(_tr("Calories", "Kalorien"), 0, 2000, 300, step=10, key="manual_calories")
        date_input = st.date_input(_tr("Activity date", "Aktivitaetsdatum"), value=datetime.now(), key="manual_activity_date")
        time_input = st.time_input(_tr("Time", "Uhrzeit"), value=datetime.now().time(), key="manual_activity_time")

        # For strength training allow selecting the performed exercises instead of a numeric training effect
        if activity_type.lower().startswith("strength"):
            exercises = [
                "BENCH_PRESS",
                "SQUAT",
                "DEADLIFT",
                "FRONT_SQUAT",
                "GOBLET_SQUAT",
                "PUSH_UP",
                "DIP",
                "PULL_UP",
                "ASSISTED_PULL_UP",
                "CHIN_UP",
                "ROW",
                "BARBELL_ROW",
                "DUMBBELL_ROW",
                "SHOULDER_PRESS",
                "LATERAL_RAISE",
                "FRONT_RAISE",
                "UPRIGHT_ROW",
                "FACE_PULL",
                "SHRUG",
                "SIDE_SHOULDER_RAISE",
                "BICEP_CURL",
                "TRICEP_EXTENSION",
                "TRICEP_DIP",
                "LAT_PULLDOWN",
                "LEG_PRESS",
                "LEG_EXTENSION",
                "HAMSTRING_CURL",
                "LEG_CURL",
                "CALF_RAISE",
                "SEATED_CALF_RAISE",
                "LUNGE",
                "WALKING_LUNGE",
                "REVERSE_LUNGE",
                "SIDE_LUNGE",
                "PLANK",
                "DEAD_BUG",
                "HOLLOW_BODY_HOLD",
                "MOUNTAIN_CLIMBER",
                "CABLE_CRUNCH",
                "HANGING_LEG_RAISE",
                "AB_WHEEL",
                "RUSSIAN_TWIST",
                "HIP_THRUST",
                "GLUTE_BRIDGE",
                "GOOD_MORNING",
                "NORDIC_CURL",
                "MUSCLE_UP",
                "BURPEE",
                "KETTLEBELL_SWING",
                "TURKISH_GET_UP",
                "FARMER_CARRY",
                "SLED_PUSH",
                "SLED_PULL",
            ]
            selected_exercises = st.multiselect(_tr("Exercises (strength)", "Uebungen (Krafttraining)"), exercises, key="manual_strength_exercises")
        else:
            training_effect = st.slider(_tr("Training effect (Garmin scale 1-5)", "Trainingseffekt (Garmin-Skala 1-5)"), 1.0, 5.0, 3.0, step=0.1, key="manual_training_effect")
    
    if st.button(_tr("Save activity", "Aktivitaet speichern"), key="save_manual_activity_btn"):
        # Combine date and time into single datetime string
        dt = datetime.combine(date_input, time_input)
        primary_metric = None
        if activity_type.lower().startswith("strength"):
            primary_metric = selected_exercises or ["UNKNOWN"]
        else:
            primary_metric = training_effect

        return {
            "date": dt.isoformat(sep=' '),
            "activity_type": activity_type,
            "duration": duration_minutes * 60,  # convert to seconds for internal format
            "distance": distance_km * 1000,  # convert to meters for internal format
            "calories": calories,
            "primary_metric": primary_metric,
        }
    
    return None
