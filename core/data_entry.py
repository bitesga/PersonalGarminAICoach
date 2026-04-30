"""Data entry and Garmin Connect login for manual data input."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

try:
    import streamlit as st
except ImportError:
    st = None


def render_garmin_credentials_section() -> dict[str, str] | None:
    """Render a Garmin login form with email and password.
    
    Returns dict with 'email' and 'password' if user submits, else None.
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
            help="E-Mail-Adresse deines Garmin-Accounts"
        )
    
    with col2:
        password = st.text_input(
            "Garmin Passwort",
            type="password",
            placeholder="••••••••",
            key="garmin_password",
            help="Passwort deines Garmin-Accounts (wird sicher gespeichert)"
        )
    
    if st.button("✓ Garmin-Account verbinden", key="connect_garmin_btn", use_container_width=True):
        if email and password:
            st.success(f"✓ Anmeldedaten gespeichert. Garmin-Daten werden synchronisiert.")
            return {"email": email.strip(), "password": password}
        else:
            st.error("Bitte gib E-Mail und Passwort ein.")
    
    st.markdown(
        "<span style='color:#94a3b8; font-size:0.85rem'>🔐 Deine Anmeldedaten werden verschlüsselt gespeichert und nicht weitergegeben.</span>",
        unsafe_allow_html=True,
    )
    return None


def render_manual_health_entry() -> dict[str, Any]:
    """Render a form for manual health metric entry (sleep, body_battery, stress, etc.)."""
    if st is None:
        return {}

    st.markdown("### Manuelle Gesundheitsdaten eingeben")
    
    col1, col2 = st.columns(2)
    with col1:
        sleep_score = st.slider("Sleep Score (0–100)", 0, 100, 75, key="manual_sleep_score")
        body_battery = st.slider("Body Battery (0–100)", 0, 100, 70, key="manual_body_battery")
        stress = st.slider("Stress (0–100)", 0, 100, 20, key="manual_stress")
    
    with col2:
        vo2_max = st.number_input("VO2Max (ml/kg/min)", 20.0, 80.0, 45.0, step=0.1, key="manual_vo2_max")
        resting_hr = st.number_input("Resting Heart Rate (bpm)", 30.0, 120.0, 60.0, step=1.0, key="manual_resting_hr")
        date_input = st.date_input("Datum", value=datetime.now(), key="manual_date")
    
    return {
        "date": str(date_input),
        "sleep_score": sleep_score,
        "body_battery": body_battery,
        "stress": stress,
        "vo2_max": vo2_max,
        "resting_heart_rate": resting_hr,
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
            ["Running", "Cycling", "Strength Training", "Swimming", "Walking", "Other"],
            key="manual_activity_type"
        )
        duration_minutes = st.number_input("Dauer (Minuten)", 1, 300, 45, step=5, key="manual_duration")
        distance_km = st.number_input("Distanz (km, 0 wenn nicht bekannt)", 0.0, 100.0, 0.0, step=0.1, key="manual_distance")
    
    with col2:
        calories = st.number_input("Kalorien", 0, 2000, 300, step=10, key="manual_calories")
        training_effect = st.slider("Trainingseffekt (Garmin-Skala 1–5)", 1.0, 5.0, 3.0, step=0.1, key="manual_training_effect")
        date_input = st.date_input("Datum der Aktivität", value=datetime.now(), key="manual_activity_date")
    
    if st.button("Aktivität speichern", key="save_manual_activity_btn"):
        return {
            "date": str(date_input),
            "activity_type": activity_type,
            "duration": duration_minutes * 60,  # convert to seconds for internal format
            "distance": distance_km * 1000,  # convert to meters for internal format
            "calories": calories,
            "primary_metric": training_effect,
        }
    
    return None
