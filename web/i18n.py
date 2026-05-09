from __future__ import annotations

import streamlit as st


SUPPORTED_LANGUAGES = ["en", "de"]


def get_language() -> str:
    lang = str(st.session_state.get("ui_language", "en")).strip().lower()
    if lang not in SUPPORTED_LANGUAGES:
        return "en"
    return lang


def tr(english: str, german: str) -> str:
    return german if get_language() == "de" else english
