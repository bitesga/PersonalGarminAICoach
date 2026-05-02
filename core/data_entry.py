"""Data entry and Garmin login for manual data input."""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    import streamlit as st
except ImportError:
    st = None


def render_garmin_credentials_section() -> dict[str, str] | None:
    """Render a Garmin login form with email and password.

    Returns a dict with email/password when the button is clicked, otherwise None.
    """
    if st is None:
        return None

    st.markdown("### Garmin Connect")
    st.write(
        "Melde dich mit deinem Garmin-Account an, um automatisch Aktivitäten und Fitnessdaten zu synchronisieren."
    )

    col1, col2 = st.columns(2)
    with col1:
        email = st.text_input(
            "Garmin E-Mail",
            placeholder="dein.name@example.com",
            key="garmin_email",
            help="E-Mail-Adresse deines Garmin-Accounts",
        )

    with col2:
        password = st.text_input(
            "Garmin Passwort",
            type="password",
            placeholder="••••••••",
            key="garmin_password",
            help="Passwort deines Garmin-Accounts",
        )

    if st.button("✓ Garmin-Account verbinden", key="connect_garmin_btn", use_container_width=True):
        if email and password:
            st.success("Garmin-Account erfolgreich vorbereitet.")
            return {"email": email.strip(), "password": password}
        st.error("Bitte gib E-Mail und Passwort ein.")
        return None

    st.markdown(
        "<span style='color:#94a3b8; font-size:0.85rem'>Hinweis: Die Anmeldedaten werden lokal für den aktiven Discord-User gespeichert.</span>",
        unsafe_allow_html=True,
    )
    return None


def render_manual_health_entry() -> dict[str, Any]:
    """Render a form for manual health metric entry (sleep, body_battery, stress, etc.)."""
    if st is None:
        return {}

    st.markdown("### Manuelle Gesundheitsdaten eingeben")

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
        sleep_score = st.slider("Sleep Score (0–100)", 0, 100, 75, key="manual_sleep_score")
        body_battery = st.slider("Body Battery (0–100)", 0, 100, 70, key="manual_body_battery")
        stress = st.slider("Stress (0–100)", 0, 100, 20, key="manual_stress")
        vo2_max = st.slider("VO2Max (ml/kg/min)", 20.0, 110.0, 45.0, step=0.1, key="manual_vo2_max")
        resting_hr = st.slider("Resting Heart Rate (bpm)", 0, 100, 60, step=1, key="manual_resting_hr")
    
    with col2:
        training_load_acute = st.number_input(
            "Acute Training Load",
            0.0,
            1000.0,
            0.0,
            step=1.0,
            key="manual_training_load_acute",
        )
        training_balance_feedback = st.selectbox(
            "Training Balance",
            training_balance_options,
            index=0,
            key="manual_training_balance_feedback",
        )
        if training_balance_feedback == "OTHER":
            training_balance_feedback = st.text_input(
                "Training Balance (frei eingeben)",
                placeholder="z. B. AEROBIC_HIGH_SHORTAGE",
                key="manual_training_balance_feedback_other",
            ).strip() or "N/A"
        date_input = st.date_input("Datum", value=datetime.now(), key="manual_date")
        time_input = st.time_input("Uhrzeit", value=datetime.now().time(), key="manual_time")
    
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

    st.markdown("### Manuelle Aktivität hinzufügen")
    
    col1, col2 = st.columns(2)
    with col1:
        activity_type = st.selectbox(
            "Aktivitätstyp",
            ["running", "cycling", "strength_training", "swimming", "walking", "other"],
            key="manual_activity_type"
        )
        duration_minutes = st.number_input("Dauer (Minuten)", 1, 300, 45, step=5, key="manual_duration")
        distance_km = st.number_input("Distanz (km, 0 wenn nicht bekannt)", 0.0, 100.0, 0.0, step=0.1, key="manual_distance")
    
    with col2:
        calories = st.number_input("Kalorien", 0, 2000, 300, step=10, key="manual_calories")
        date_input = st.date_input("Datum der Aktivität", value=datetime.now(), key="manual_activity_date")
        time_input = st.time_input("Uhrzeit", value=datetime.now().time(), key="manual_activity_time")

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
            selected_exercises = st.multiselect("Übungen (Strength)", exercises, key="manual_strength_exercises")
        else:
            training_effect = st.slider("Trainingseffekt (Garmin-Skala 1–5)", 1.0, 5.0, 3.0, step=0.1, key="manual_training_effect")
    
    if st.button("Aktivität speichern", key="save_manual_activity_btn"):
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
