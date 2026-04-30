"""Handle persistence of Garmin data to JSON files."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any


def _ensure_data_dir() -> Path:
    """Ensure data directory exists."""
    data_dir = Path(__file__).resolve().parents[1] / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir


def save_daily_stats(data: dict[str, Any]) -> Path:
    """Save 7-day daily stats to JSON file."""
    data_dir = _ensure_data_dir()
    filename = data_dir / "daily_stats.json"
    
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


def save_activities(data: list[dict[str, Any]]) -> Path:
    """Save 7-activity history to JSON file."""
    data_dir = _ensure_data_dir()
    filename = data_dir / "activities.json"
    
    output = {
        "last_updated": datetime.now().isoformat(),
        "activities": data,
    }
    
    json_str = json.dumps(output, indent=2, ensure_ascii=False, default=str)
    filename.write_text(json_str, encoding="utf-8")
    return filename


def load_daily_stats() -> dict[str, Any]:
    """Load existing daily stats from JSON file."""
    data_dir = _ensure_data_dir()
    filename = data_dir / "daily_stats.json"
    
    if not filename.exists():
        return {}
    
    try:
        return json.loads(filename.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_activities() -> list[dict[str, Any]]:
    """Load existing activities from JSON file."""
    data_dir = _ensure_data_dir()
    filename = data_dir / "activities.json"
    
    if not filename.exists():
        return []
    
    try:
        data = json.loads(filename.read_text(encoding="utf-8"))
        return data.get("activities", [])
    except json.JSONDecodeError:
        return []


def save_user_profile(profile: dict[str, Any]) -> Path:
    """Save the dashboard user profile and preferences to JSON."""
    data_dir = _ensure_data_dir()
    filename = data_dir / "user_profile.json"
    output = {
        "last_updated": datetime.now().isoformat(),
        "profile": profile,
    }
    filename.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return filename


def load_user_profile() -> dict[str, Any]:
    """Load the persisted user profile and preferences."""
    data_dir = _ensure_data_dir()
    filename = data_dir / "user_profile.json"

    if not filename.exists():
        return {}

    try:
        data = json.loads(filename.read_text(encoding="utf-8"))
        profile = data.get("profile", {})
        return profile if isinstance(profile, dict) else {}
    except json.JSONDecodeError:
        return {}
