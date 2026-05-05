from __future__ import annotations

import json
import os
import random
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import streamlit as st

from core import user_management
from core.data_persistence import load_user_profile, save_user_profile
from core.notification_service import send_email, send_verification_dm

ROOT_DIR = Path(__file__).resolve().parents[1]
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
    tokens[token] = {
        "user_id": str(user_id).strip(),
        "expires_at": (datetime.utcnow() + timedelta(days=days)).isoformat(),
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


def _persist_auth_session(user_id: str) -> None:
    _set_auth_query_param(_issue_auth_token(user_id=user_id, days=3))


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


def _resolve_verify_email_password():
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


def _resolve_verify_discord_password():
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


def render_auth_gate() -> str:
    st.session_state.setdefault("discord_verified", False)
    st.session_state.setdefault("temp_discord_id", "")
    st.session_state.setdefault("temp_code_input", "")
    st.session_state.setdefault("temp_code_sent", False)
    st.session_state.setdefault("active_discord_id", "")

    if not st.session_state.get("discord_verified") and _restore_session_from_token():
        st.rerun()

    if not st.session_state.get("discord_verified"):
        st.markdown("### Sign In / Verification")
        st.caption("Choose a sign-in method: email or Discord.")

        method = st.radio("Choose sign-in method", options=["Email", "Discord"], index=0, horizontal=True)

        st.session_state.setdefault("temp_email", "")
        st.session_state.setdefault("temp_code_input", "")
        st.session_state.setdefault("temp_code_sent", False)

        if method == "Discord":
            st.caption("Discord sign-in with a clear separation between login and registration.")
            st.info("Please join the Discord server first: https://discord.gg/DPMpqmEaN7")

            tab_login, tab_register = st.tabs(["I already have an account", "Register a new account"])

            with tab_login:
                discord_id = st.text_input("Discord user ID (numeric)", value=st.session_state.get("temp_discord_id", ""), key="reg_discord_id_field")
                discord_password = st.text_input("Password", type="password", key="reg_discord_password_field")
                st.session_state["temp_discord_id"] = discord_id

                if st.button("Sign in with Discord and password", key="discord_login_btn"):
                    if not discord_id:
                        st.error("Please enter your Discord user ID.")
                    elif not discord_password:
                        st.error("Please enter your password.")
                    else:
                        resolved_key, resolved_user = user_management.get_user_login_record_for_discord_id(str(discord_id).strip())
                        verifier = _resolve_verify_discord_password()
                        if not verifier(str(discord_id).strip(), discord_password):
                            st.error("Discord sign-in failed. The user ID or password is incorrect.")
                        else:
                            login_key = resolved_key or str(discord_id).strip()
                            profile = load_user_profile(user_id=login_key) or {}
                            if isinstance(resolved_user, dict):
                                profile.update({
                                    "discord_user_id": str(discord_id).strip(),
                                    "email": str(resolved_user.get("email", profile.get("email", ""))).strip(),
                                })
                            else:
                                profile["discord_user_id"] = str(discord_id).strip()
                            save_user_profile(profile, user_id=login_key)
                            st.session_state.discord_verified = True
                            st.session_state.active_discord_id = login_key
                            _persist_auth_session(login_key)
                            st.success("Sign-in successful - you are being redirected to the dashboard.")
                            st.rerun()

            with tab_register:
                discord_id_reg = st.text_input("Discord user ID for registration", value=st.session_state.get("temp_discord_id", ""), key="reg_discord_id_register_field")
                discord_password_reg = st.text_input("Password for new Discord sign-in", type="password", key="reg_discord_password_register_field")
                st.session_state["temp_discord_id"] = discord_id_reg

                if st.button("Register & send code", key="reg_send_code_btn"):
                    if not discord_id_reg:
                        st.error("Please enter a Discord user ID.")
                    else:
                        discord_id_clean = str(discord_id_reg).strip()
                        existing_discord_user = user_management.get_user(discord_id_clean)
                        if existing_discord_user and bool(existing_discord_user.get("verified", False)):
                            st.warning("This Discord ID is already registered and verified. Please use the sign-in tab.")
                        else:
                            user = user_management.register_user(discord_id_clean, password=discord_password_reg or None)
                            code = user.get("verification_code")
                            if not code:
                                st.error("No verification code available. Please try again or contact support.")
                            else:
                                invite = os.getenv("DISCORD_SERVER_INVITE", "https://discord.gg/DPMpqmEaN7")
                                sent, msg = send_verification_dm(discord_id_clean, str(code), invite_link=invite)
                                if sent:
                                    st.success("Verification code sent by DM. Please check Discord.")
                                    st.session_state["temp_code_sent"] = True
                                else:
                                    msg_lower = str(msg).lower()
                                    no_mutual_guild = ("no mutual guilds" in msg_lower) or ("50278" in msg_lower)
                                    if no_mutual_guild:
                                        st.warning("Please join our Discord server first so I can send you a DM.")
                                        st.markdown("Join the server: https://discord.gg/DPMpqmEaN7")
                                    else:
                                        st.error(f"Send failed: {msg}")

                entered = st.text_input("Verification code", value=st.session_state.get("temp_code_input", ""), key="reg_code_input_field")
                st.session_state["temp_code_input"] = entered
                if st.button("Verify code", key="reg_verify_btn"):
                    if not discord_id_reg:
                        st.error("Enter your Discord user ID first.")
                    elif not entered:
                        st.error("Please enter the code.")
                    else:
                        ok = user_management.verify_user(str(discord_id_reg).strip(), str(entered).strip())
                        if ok:
                            st.session_state.discord_verified = True
                            st.session_state.active_discord_id = str(discord_id_reg).strip()
                            profile = load_user_profile(user_id=str(discord_id_reg).strip()) or {}
                            profile["discord_user_id"] = str(discord_id_reg).strip()
                            profile["notify_discord"] = True
                            save_user_profile(profile, user_id=str(discord_id_reg).strip())
                            _persist_auth_session(str(discord_id_reg).strip())
                            st.success("Verified - you are being redirected to the dashboard.")
                            st.balloons()
                            st.rerun()
                        else:
                            st.error("Verification failed. The code is invalid or expired.")

        elif method == "Email":
            st.caption("Email sign-in with a clear separation between login and registration.")
            tab_login, tab_register = st.tabs(["I already have an account", "Register a new account"])

            with tab_login:
                email = st.text_input("Email", value=st.session_state.get("temp_email", ""), key="reg_email_field")
                password = st.text_input("Password", type="password", key="reg_password_field")
                st.session_state["temp_email"] = email

                if st.button("Sign in with email and password", key="email_login_btn"):
                    if not email:
                        st.error("Please enter your email address.")
                    elif not password:
                        st.error("Please enter your password.")
                    else:
                        user = user_management.get_user_by_email(email.strip().lower())
                        if not user:
                            st.error("No email account found. Please register first.")
                        elif not user.get("verified"):
                            st.error("Your email account is not verified yet. Please confirm the code first.")
                        else:
                            verifier = _resolve_verify_email_password()
                            if not verifier(email.strip().lower(), password):
                                st.error("Passwort ist falsch.")
                            else:
                                key = user_management._key_for_email(email.strip().lower())
                                st.session_state.discord_verified = True
                                st.session_state.active_discord_id = key
                                profile = load_user_profile(user_id=key) or {}
                                profile["email"] = email.strip().lower()
                                profile["notify_email"] = True
                                save_user_profile(profile, user_id=key)
                                _persist_auth_session(key)
                                st.success("Sign-in successful - you are being redirected to the dashboard.")
                                st.rerun()

            with tab_register:
                email_reg = st.text_input("Email for registration", value=st.session_state.get("temp_email", ""), key="reg_email_register_field")
                password_reg = st.text_input("Password for new email sign-in", type="password", key="reg_password_register_field")
                st.session_state["temp_email"] = email_reg

                if st.button("Register & send code by email", key="reg_email_send_btn"):
                    if not email_reg:
                        st.error("Please enter an email address.")
                    else:
                        email_clean = email_reg.strip().lower()
                        existing_email_user = user_management.get_user_by_email(email_clean)
                        if existing_email_user and bool(existing_email_user.get("verified", False)):
                            st.warning("This email is already registered and verified. Please use the sign-in tab.")
                        else:
                            user = user_management.register_email_user(email_clean, password=password_reg or None)
                            code = user.get("verification_code")
                            if not code:
                                st.error("No verification code available. Please try again.")
                            else:
                                subject = "Your verification code for PersonalGarminAICoach"
                                text = f"Your verification code: {code}\n\nEnter this code in the app form to verify your account."
                                html = f"<p>Your verification code: <strong>{code}</strong></p>"
                                sent, msg = send_email(subject=subject, body_text=text, body_html=html, recipient_email=email_clean)
                                if sent:
                                    st.success("Verification code sent by email. Please check your inbox.")
                                    st.session_state["temp_code_sent"] = True
                                else:
                                    st.error(f"Email send failed: {msg}")

                entered = st.text_input("Verification code", value=st.session_state.get("temp_code_input", ""), key="reg_email_code_input_field")
                st.session_state["temp_code_input"] = entered
                if st.button("Verify code (email)", key="reg_email_verify_btn"):
                    if not email_reg:
                        st.error("Enter your email address first.")
                    elif not entered:
                        st.error("Please enter the code.")
                    else:
                        ok = user_management.verify_email_user(email_reg.strip().lower(), str(entered).strip())
                        if ok:
                            key = user_management._key_for_email(email_reg.strip().lower())
                            st.session_state.discord_verified = True
                            st.session_state.active_discord_id = key
                            profile = load_user_profile(user_id=key) or {}
                            profile["email"] = email_reg.strip().lower()
                            profile["notify_email"] = True
                            save_user_profile(profile, user_id=key)
                            _persist_auth_session(key)
                            st.success("Email verified - you are being redirected to the dashboard.")
                            st.balloons()
                            st.rerun()
                        else:
                            st.error("Verification failed. The code is invalid or expired.")

        st.stop()

    return str(st.session_state.get("active_discord_id", "")).strip()
