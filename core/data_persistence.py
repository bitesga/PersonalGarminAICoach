"""Handle persistence of Garmin data to JSON files."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any


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
