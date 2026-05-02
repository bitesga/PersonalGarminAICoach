"""Simple user management backed by a JSON file.

Provides:
- register_user(discord_id)
- generate_verification_code()
- verify_user(discord_id, code)
- get_user(discord_id)
- update_user(discord_id, data)
- list_users()

This module uses a small file-locking strategy to avoid concurrent writes.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from .coach_agent import DATA_DIR


USERS_PATH = DATA_DIR / "users.json"


def _ensure_users_file() -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not USERS_PATH.exists():
        USERS_PATH.write_text(json.dumps({}), encoding="utf-8")


def _lock_file(f):
    # cross-platform advisory lock
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_RLCK, 1)
        except Exception:
            pass
    else:
        import fcntl

        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass


def _unlock_file(f):
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
    else:
        import fcntl

        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass


def _load_users() -> dict[str, Any]:
    _ensure_users_file()
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            _lock_file(f)
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
            finally:
                _unlock_file(f)
    except FileNotFoundError:
        data = {}
    return data if isinstance(data, dict) else {}


def _save_users(users: dict[str, Any]) -> None:
    _ensure_users_file()
    dirpath = USERS_PATH.parent
    fd, tmp = tempfile.mkstemp(dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, USERS_PATH)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def generate_verification_code() -> str:
    return f"{random.randint(100000, 999999)}"


def _key_for_email(email: str) -> str:
    """Return canonical key used to store email-based users in users.json."""
    return f"email:{str(email).strip().lower()}"


def _normalize_link_target(link_type: str, target_value: str) -> str:
    link_type = str(link_type).strip().lower()
    target_value = str(target_value).strip()
    if link_type == "email":
        return target_value.lower()
    return target_value


def request_contact_link(user_id: str, link_type: str, target_value: str) -> dict[str, Any]:
    """Create or refresh a 6-digit code used to link a second contact method.

    The pending code is stored on the current user record until it is verified.
    """
    users = _load_users()
    user = users.get(user_id)
    if not user:
        return {}

    normalized_target = _normalize_link_target(link_type, target_value)
    if not normalized_target:
        return dict(user)

    pending_link = {
        "type": str(link_type).strip().lower(),
        "target": normalized_target,
        "verification_code": generate_verification_code(),
        "requested_at": datetime.utcnow().isoformat(),
    }
    user["pending_link"] = pending_link
    users[user_id] = user
    _save_users(users)
    return dict(user)


def verify_contact_link(user_id: str, link_type: str, target_value: str, code: str) -> bool:
    """Verify a pending contact-link code and persist the linked contact on the user."""
    users = _load_users()
    user = users.get(user_id)
    if not user:
        return False

    pending_link = user.get("pending_link")
    if not isinstance(pending_link, dict):
        return False

    normalized_type = str(link_type).strip().lower()
    normalized_target = _normalize_link_target(link_type, target_value)
    expected_code = str(pending_link.get("verification_code", "")).strip()

    if pending_link.get("type") != normalized_type:
        return False
    if str(pending_link.get("target", "")).strip() != normalized_target:
        return False
    if not expected_code or expected_code != str(code).strip():
        return False

    if normalized_type == "email":
        user["linked_email"] = normalized_target
    elif normalized_type == "discord":
        user["linked_discord_id"] = normalized_target
    else:
        return False

    user.pop("pending_link", None)
    user["linked_at"] = datetime.utcnow().isoformat()
    users[user_id] = user
    _save_users(users)
    return True


def register_email_user(email: str, password: str | None = None) -> dict[str, Any]:
    """Register (or refresh) an email-based user.

    Stores optional password hash (PBKDF2-SHA256) when `password` is provided.
    """
    import hashlib

    users = _load_users()
    key = _key_for_email(email)
    user = users.get(key)
    now = datetime.utcnow().isoformat()

    if user is None:
        code = generate_verification_code()
        auth = None
        if password:
            salt = os.urandom(16).hex()
            pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
            auth = {"salt": salt, "password_hash": pwd_hash}

        user = {
            "email": email,
            "verified": False,
            "verification_code": code,
            "created_at": now,
            "verified_at": None,
            "garmin_connected": False,
            "profile": {},
            "auth": auth,
        }
        users[key] = user
        _save_users(users)
        return user

    # existing user: refresh code if not verified
    if not user.get("verified"):
        user["verification_code"] = generate_verification_code()
        user["created_at"] = now
        # update password if provided
        if password:
            salt = os.urandom(16).hex()
            pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
            user["auth"] = {"salt": salt, "password_hash": pwd_hash}
        users[key] = user
        _save_users(users)
    return user


def request_verification_for_email(email: str) -> dict[str, Any]:
    users = _load_users()
    key = _key_for_email(email)
    user = users.get(key)
    now = datetime.utcnow().isoformat()
    if user is None:
        code = generate_verification_code()
        user = {
            "email": email,
            "verified": False,
            "verification_code": code,
            "created_at": now,
            "verified_at": None,
            "garmin_connected": False,
            "profile": {},
        }
        users[key] = user
        _save_users(users)
        return user

    user["verified"] = False
    user["verification_code"] = generate_verification_code()
    user["created_at"] = now
    user.pop("verified_at", None)
    users[key] = user
    _save_users(users)
    return user


def verify_email_user(email: str, code: str) -> bool:
    users = _load_users()
    key = _key_for_email(email)
    user = users.get(key)
    if not user:
        return False
    if user.get("verified"):
        return True
    expected = str(user.get("verification_code", ""))
    if expected and expected == str(code).strip():
        user["verified"] = True
        user["verified_at"] = datetime.utcnow().isoformat()
        user.pop("verification_code", None)
        users[key] = user
        _save_users(users)
        return True
    return False


def get_user_by_email(email: str) -> dict[str, Any] | None:
    users = _load_users()
    return users.get(_key_for_email(email))


def verify_email_password(email: str, password: str) -> bool:
    """Verify an email user's password against the stored PBKDF2 hash."""
    import hashlib

    user = get_user_by_email(email)
    if not user:
        return False

    auth = user.get("auth")
    if not isinstance(auth, dict):
        return False

    salt = str(auth.get("salt", "")).strip()
    stored_hash = str(auth.get("password_hash", "")).strip()
    if not salt or not stored_hash:
        return False

    derived_hash = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()
    return derived_hash == stored_hash


def register_user(discord_id: str, password: str | None = None) -> dict[str, Any]:
    """Register a user by Discord ID and return the user record.

    If the user already exists, returns the existing record (and refreshes the code if unverified).
    """
    import hashlib

    users = _load_users()
    user = users.get(discord_id)
    now = datetime.utcnow().isoformat()
    if user is None:
        code = generate_verification_code()
        auth = None
        if password:
            salt = os.urandom(16).hex()
            pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
            auth = {"salt": salt, "password_hash": pwd_hash}
        user = {
            "discord_id": discord_id,
            "verified": False,
            "verification_code": code,
            "created_at": now,
            "verified_at": None,
            "garmin_connected": False,
            "profile": {},
            "auth": auth,
        }
        users[discord_id] = user
        _save_users(users)
        return user

    # existing user: if not verified, refresh code and timestamp
    if not user.get("verified"):
        user["verification_code"] = generate_verification_code()
        user["created_at"] = now
        if password:
            salt = os.urandom(16).hex()
            pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
            user["auth"] = {"salt": salt, "password_hash": pwd_hash}
        users[discord_id] = user
        _save_users(users)
    return user


def request_verification(discord_id: str) -> dict[str, Any]:
    """Force generation of a new verification code and mark user as unverified.

    Returns the user record (new or updated).
    """
    users = _load_users()
    user = users.get(discord_id)
    now = datetime.utcnow().isoformat()
    if user is None:
        # Create a fresh unverified user with a code
        code = generate_verification_code()
        user = {
            "discord_id": discord_id,
            "verified": False,
            "verification_code": code,
            "created_at": now,
            "verified_at": None,
            "garmin_connected": False,
            "profile": {},
        }
        users[discord_id] = user
        _save_users(users)
        return user

    # existing user: always refresh code and mark unverified
    user["verified"] = False
    user["verification_code"] = generate_verification_code()
    user["created_at"] = now
    user.pop("verified_at", None)
    users[discord_id] = user
    _save_users(users)
    return user


def verify_user(discord_id: str, code: str) -> bool:
    users = _load_users()
    user = users.get(discord_id)
    if not user:
        return False
    if user.get("verified"):
        return True
    expected = str(user.get("verification_code", ""))
    if expected and expected == str(code).strip():
        user["verified"] = True
        user["verified_at"] = datetime.utcnow().isoformat()
        user.pop("verification_code", None)
        users[discord_id] = user
        _save_users(users)
        return True
    return False


def get_user(discord_id: str) -> dict[str, Any] | None:
    users = _load_users()
    user = users.get(discord_id)
    return dict(user) if isinstance(user, dict) else None


def _password_matches_auth(auth: Any, password: str) -> bool:
    import hashlib

    if not isinstance(auth, dict):
        return False

    salt = str(auth.get("salt", "")).strip()
    stored_hash = str(auth.get("password_hash", "")).strip()
    if not salt or not stored_hash:
        return False

    derived_hash = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()
    return derived_hash == stored_hash


def verify_discord_password(discord_id: str, password: str) -> bool:
    """Verify a Discord user's password against the stored PBKDF2 hash."""
    discord_id = str(discord_id).strip()

    users = _load_users()
    direct_user = users.get(discord_id)
    if isinstance(direct_user, dict) and _password_matches_auth(direct_user.get("auth"), password):
        return True

    for record in users.values():
        if not isinstance(record, dict):
            continue
        if str(record.get("linked_discord_id", "")).strip() != discord_id:
            continue
        if _password_matches_auth(record.get("auth"), password):
            return True

    return False


def get_user_login_record_for_discord_id(discord_id: str) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve the user record that should back a Discord-ID login.

    Returns the storage key and the user record. If the Discord ID belongs to a
    native Discord account, that record is returned first. Otherwise, an email
    account linked to the Discord ID is returned.
    """
    users = _load_users()
    direct_user = users.get(discord_id)
    if isinstance(direct_user, dict) and isinstance(direct_user.get("auth"), dict):
        return discord_id, dict(direct_user)

    for key, record in users.items():
        if not isinstance(record, dict):
            continue
        if not isinstance(record.get("auth"), dict):
            continue
        if str(record.get("linked_discord_id", "")).strip() == str(discord_id).strip():
            return str(key), dict(record)

    if isinstance(direct_user, dict):
        return discord_id, dict(direct_user)

    return None, None


def update_user(discord_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    users = _load_users()
    user = users.get(discord_id)
    if not user:
        return None
    user.update(data)
    users[discord_id] = user
    _save_users(users)
    return dict(user)


def list_users() -> list[dict[str, Any]]:
    users = _load_users()
    return [dict(v) for v in users.values()]
