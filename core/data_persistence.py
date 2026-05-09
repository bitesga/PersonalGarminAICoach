"""Handle persistence of Garmin data to JSON files."""

from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
from typing import Any
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def _ensure_data_dir() -> Path:
    """Ensure data directory exists."""
    data_dir = Path(__file__).resolve().parents[1] / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir


def _safe_user_segment(user_id: str) -> str:
    """Create a filesystem-safe user segment from Discord ID."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(user_id).strip())


def _resolve_file(filename: str, user_id: str | None = None) -> Path:
    data_dir = _ensure_data_dir()
    if not user_id:
        return data_dir / filename
    user_dir = data_dir / "users" / _safe_user_segment(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / filename


def list_user_ids() -> list[str]:
    """List user ids based on existing user data directories."""
    data_dir = _ensure_data_dir() / "users"
    if not data_dir.exists():
        return []
    return [item.name for item in data_dir.iterdir() if item.is_dir()]


def save_daily_stats(data: dict[str, Any], user_id: str | None = None) -> Path:
    """Save 7-day daily stats to JSON file."""
    filename = _resolve_file("daily_stats.json", user_id=user_id)
    
    # Preserve existing data and append/update
    existing = {}
    if filename.exists():
        try:
            existing = json.loads(filename.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    
    # Merge new data (today's stats overwrite existing)
    existing.update(data)
    
    output = json.dumps(existing, indent=2, ensure_ascii=False, default=str)
    filename.write_text(output, encoding="utf-8")
    return filename


def delete_daily_stat(date_key: str, user_id: str | None = None) -> Path:
    """Delete a daily stat entry by date key for a specific user."""
    filename = _resolve_file("daily_stats.json", user_id=user_id)
    if not filename.exists():
        return filename

    try:
        existing = json.loads(filename.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        existing = {}

    if isinstance(existing, dict) and date_key in existing:
        existing.pop(date_key, None)
        filename.write_text(json.dumps(existing, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return filename


def save_activities(data: list[dict[str, Any]], user_id: str | None = None) -> Path:
    """Save 7-activity history to JSON file."""
    filename = _resolve_file("activities.json", user_id=user_id)
    
    output = {
        "last_updated": datetime.now().isoformat(),
        "activities": data,
    }
    
    json_str = json.dumps(output, indent=2, ensure_ascii=False, default=str)
    filename.write_text(json_str, encoding="utf-8")
    return filename


def delete_activity(activity_id: str, user_id: str | None = None) -> Path:
    """Delete an activity entry by id for a specific user."""
    filename = _resolve_file("activities.json", user_id=user_id)
    if not filename.exists():
        return filename

    try:
        payload = json.loads(filename.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {}

    activities = payload.get("activities", []) if isinstance(payload, dict) else []
    if not isinstance(activities, list):
        activities = []

    filtered = [item for item in activities if str(item.get("id", "")) != str(activity_id)]
    payload["activities"] = filtered
    payload["last_updated"] = datetime.now().isoformat()
    filename.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return filename


def load_daily_stats(user_id: str | None = None) -> dict[str, Any]:
    """Load existing daily stats from JSON file."""
    filename = _resolve_file("daily_stats.json", user_id=user_id)
    
    if not filename.exists():
        return {}
    
    try:
        return json.loads(filename.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_activities(user_id: str | None = None) -> list[dict[str, Any]]:
    """Load existing activities from JSON file."""
    filename = _resolve_file("activities.json", user_id=user_id)
    
    if not filename.exists():
        return []
    
    try:
        data = json.loads(filename.read_text(encoding="utf-8"))
        return data.get("activities", [])
    except json.JSONDecodeError:
        return []


def save_user_profile(profile: dict[str, Any], user_id: str | None = None) -> Path:
    """Save the dashboard user profile and preferences to JSON."""
    filename = _resolve_file("user_profile.json", user_id=user_id)
    output = {
        "last_updated": datetime.now().isoformat(),
        "profile": profile,
    }
    filename.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return filename


def load_user_profile(user_id: str | None = None) -> dict[str, Any]:
    """Load the persisted user profile and preferences."""
    filename = _resolve_file("user_profile.json", user_id=user_id)

    if not filename.exists():
        return {}

    try:
        data = json.loads(filename.read_text(encoding="utf-8"))
        profile = data.get("profile", {})
        return profile if isinstance(profile, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_garmin_credentials(credentials: dict[str, Any], user_id: str | None = None) -> None:
    """Save Garmin login credentials for a specific user.

    Credentials are stored in Vault only.
    """
    if not _save_garmin_credentials_to_vault(credentials, user_id=user_id):
        raise RuntimeError("Failed to save Garmin credentials to Vault")
        # Clear Garmin retry backoff state when credentials are updated
        save_garmin_retry_state({}, user_id=user_id)

def _save_garmin_credentials_to_vault(credentials: dict[str, Any], user_id: str | None = None) -> bool:
    """Save Garmin credentials to Vault KV store.
    
    Returns True if successful, False otherwise.
    Does not raise exceptions—logs and continues on failure.
    """
    vault_addr = os.getenv("VAULT_ADDR", "").strip()
    vault_token = os.getenv("VAULT_TOKEN", "").strip()
    if not vault_addr or not vault_token:
        logger.debug("Vault write skipped: VAULT_ADDR or VAULT_TOKEN not set")
        return False

    kv_path = os.getenv("VAULT_KV_PATH", "kv/garmin/default").strip()
    original_path = kv_path
    if "{user_id}" in kv_path and user_id:
        kv_path = kv_path.replace("{user_id}", _safe_user_segment(user_id))
        logger.debug(f"Vault write path template expanded: {original_path} → {kv_path}")

    url = _vault_build_url(vault_addr, kv_path)
    logger.debug(f"Vault write URL: {url.replace(vault_token, '***TOKEN***')}")

    # KV v2 API expects data wrapped in "data" key
    payload = {"data": credentials}
    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "X-Vault-Token": vault_token,
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            logger.info(f"Credentials saved to Vault: {credentials.get('email')} (status {response.status})")
            return True
    except urllib.error.HTTPError as e:
        logger.warning(f"Vault write HTTP error {e.code}: {e.reason} at {url.replace(vault_token, '***')}")
        return False
    except urllib.error.URLError as e:
        logger.warning(f"Vault write connection error: {e.reason}")
        return False
    except Exception as e:
        logger.warning(f"Vault write error: {type(e).__name__}: {e}")
        return False


def _vault_build_url(vault_addr: str, kv_path: str) -> str:
    base = vault_addr.rstrip("/")
    path = kv_path.lstrip("/")
    if path.startswith("v1/"):
        return f"{base}/{path}"
    if "data/" not in path:
        if "/" in path:
            mount, rest = path.split("/", 1)
            path = f"{mount}/data/{rest}"
        else:
            path = f"{path}/data"
    return f"{base}/v1/{path}"


def _load_garmin_credentials_from_vault(user_id: str | None = None) -> dict[str, Any] | None:
    vault_addr = os.getenv("VAULT_ADDR", "").strip()
    vault_token = os.getenv("VAULT_TOKEN", "").strip()
    if not vault_addr or not vault_token:
        logger.debug("Vault disabled: VAULT_ADDR or VAULT_TOKEN not set")
        return None

    kv_path = os.getenv("VAULT_KV_PATH", "kv/garmin/default").strip()
    original_path = kv_path
    if "{user_id}" in kv_path and user_id:
        kv_path = kv_path.replace("{user_id}", _safe_user_segment(user_id))
        logger.debug(f"Vault path template expanded: {original_path} → {kv_path}")
    else:
        logger.debug(f"Vault path (no user_id template): {kv_path}")

    url = _vault_build_url(vault_addr, kv_path)
    logger.debug(f"Vault URL: {url.replace(vault_token, '***TOKEN***')}")
    
    request = urllib.request.Request(
        url,
        headers={"X-Vault-Token": vault_token},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            logger.debug(f"Vault response status: {response.status}")
    except urllib.error.HTTPError as e:
        logger.warning(f"Vault HTTP error {e.code}: {e.reason} at {url.replace(vault_token, '***')}")
        return None
    except urllib.error.URLError as e:
        logger.warning(f"Vault connection error: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Vault JSON decode error: {e}")
        return None
    except Exception as e:
        logger.warning(f"Vault read error: {type(e).__name__}: {e}")
        return None

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if isinstance(data, dict) and "data" in data:
        data = data.get("data", {})
    if not isinstance(data, dict):
        logger.warning(f"Vault response data malformed: expected dict, got {type(data).__name__}")
        return None

    email = data.get("email") or data.get("GARMIN_EMAIL")
    password = data.get("password") or data.get("GARMIN_PASSWORD")
    if not email or not password:
        logger.warning("Vault response missing email or password key")
        return None
    
    logger.info(f"Credentials loaded from Vault: {email}")
    return {"email": str(email).strip(), "password": str(password).strip()}


def load_garmin_credentials(user_id: str | None = None) -> dict[str, Any]:
    """Load stored Garmin login credentials for a specific user."""
    vault_credentials = _load_garmin_credentials_from_vault(user_id=user_id)
    if vault_credentials:
        logger.debug(f"Using Vault credentials for user {user_id}")
        return vault_credentials
    logger.debug("No Vault credentials available")
    return {}


def save_coach_recommendation(recommendation: dict[str, Any], user_id: str | None = None) -> Path:
    """Save a cached coach recommendation for a specific user.

    The payload includes a generated timestamp and the recommendation dict.
    """
    filename = _resolve_file("coach_recommendation.json", user_id=user_id)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "recommendation": recommendation,
    }
    filename.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return filename


def load_coach_recommendation(user_id: str | None = None) -> dict[str, Any] | None:
    """Load a cached coach recommendation for a specific user, or return None."""
    filename = _resolve_file("coach_recommendation.json", user_id=user_id)
    if not filename.exists():
        return None
    try:
        payload = json.loads(filename.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    recommendation = payload.get("recommendation")
    generated_at = payload.get("generated_at")
    if not recommendation or not generated_at:
        return None
    try:
        # Validate ISO timestamp
        _ = datetime.fromisoformat(str(generated_at))
    except Exception:
        return None
    return payload


def save_garmin_retry_state(retry_state: dict[str, Any], user_id: str | None = None) -> Path:
    """Save Garmin retry state for rate-limit handling."""
    filename = _resolve_file("garmin_retry_state.json", user_id=user_id)
    output = {
        "updated_at": datetime.now().isoformat(),
        "state": retry_state,
    }
    filename.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.debug(f"Retry state saved: {retry_state}")
    return filename


def load_garmin_retry_state(user_id: str | None = None) -> dict[str, Any]:
    """Load Garmin retry state, or return empty dict if none exists."""
    filename = _resolve_file("garmin_retry_state.json", user_id=user_id)
    
    if not filename.exists():
        return {}
    
    try:
        data = json.loads(filename.read_text(encoding="utf-8"))
        state = data.get("state", {})
        return state if isinstance(state, dict) else {}
    except json.JSONDecodeError:
        return {}
