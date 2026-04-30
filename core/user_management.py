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


def register_user(discord_id: str) -> dict[str, Any]:
    """Register a user by Discord ID and return the user record.

    If the user already exists, returns the existing record (and refreshes the code if unverified).
    """
    users = _load_users()
    user = users.get(discord_id)
    now = datetime.utcnow().isoformat()
    if user is None:
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

    # existing user: if not verified, refresh code and timestamp
    if not user.get("verified"):
        user["verification_code"] = generate_verification_code()
        user["created_at"] = now
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
