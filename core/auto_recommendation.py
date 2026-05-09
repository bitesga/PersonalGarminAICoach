from __future__ import annotations

import inspect
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime, time as time_type
from pathlib import Path
from typing import Any

from core import coach_agent
from core.data_persistence import (
    list_user_ids,
    load_activities,
    load_daily_stats,
    load_user_profile,
    save_user_profile,
)
from core.notification_service import notify_recommendation
from core.weather_service import fetch_current_weather


ROOT_DIR = Path(__file__).resolve().parents[1]
FETCH_SCRIPT = ROOT_DIR / "core" / "fetch_garmin_data.py"
LOG_PATH = ROOT_DIR / "data" / "app.log"
DEFAULT_TIMES = ["09:00", "15:00"]

_SCHEDULER_STARTED = False
_RUN_LOCK = threading.Lock()


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("personal_garmin_ai_coach.auto")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def _parse_time_str(value: str) -> time_type | None:
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        return None


def _normalize_times(raw_times: Any) -> list[str]:
    if not isinstance(raw_times, list) or not raw_times:
        raw_times = DEFAULT_TIMES
    normalized: list[str] = []
    for entry in raw_times:
        time_obj = _parse_time_str(str(entry))
        if time_obj:
            normalized.append(time_obj.strftime("%H:%M"))
    return sorted(set(normalized))


def _get_last_run_map(profile: dict[str, Any]) -> dict[str, str]:
    raw = profile.get("auto_recommendation_last_run", {})
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if value}


def _due_times(now: datetime, times: list[str], last_run_map: dict[str, str]) -> list[str]:
    due: list[str] = []
    for time_str in times:
        time_obj = _parse_time_str(time_str)
        if not time_obj:
            continue
        scheduled_dt = datetime.combine(now.date(), time_obj)
        last_raw = last_run_map.get(time_str)
        last_dt = None
        if last_raw:
            try:
                last_dt = datetime.fromisoformat(last_raw)
            except ValueError:
                last_dt = None
        if now >= scheduled_dt and (last_dt is None or last_dt < scheduled_dt):
            due.append(time_str)
    return due


def _reload_garmin_data(user_id: str) -> tuple[bool, str]:
    if not FETCH_SCRIPT.exists():
        return False, "Garmin fetch script missing."
    command = [sys.executable, "-m", "core.fetch_garmin_data", "--user-id", str(user_id)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT_DIR), check=False)
    except Exception as exc:
        return False, f"Garmin fetch failed: {exc}"

    output_parts = []
    if result.stdout.strip():
        output_parts.append(result.stdout.strip())
    if result.stderr.strip():
        output_parts.append(result.stderr.strip())

    combined_output = "\n\n".join(output_parts) if output_parts else "Garmin data refreshed."
    return result.returncode == 0, combined_output


def _invoke_get_coach_recommendation(profile: coach_agent.CoachProfile, daily_stats, activities, user_id: str) -> dict[str, Any]:
    func = coach_agent.get_coach_recommendation
    profile_data = load_user_profile(user_id=user_id) or {}
    language = str(profile_data.get("ui_language", "en")).strip().lower()
    try:
        sig = inspect.signature(func)
        if "user_id" in sig.parameters and "weather" in sig.parameters and "language" in sig.parameters:
            return func(
                profile=profile,
                daily_stats=daily_stats,
                activities=activities,
                refresh=True,
                user_id=user_id,
                weather=_get_weather_from_profile(user_id),
                language=language,
            )
        if "user_id" in sig.parameters and "language" in sig.parameters:
            return func(
                profile=profile,
                daily_stats=daily_stats,
                activities=activities,
                refresh=True,
                user_id=user_id,
                language=language,
            )
        if "weather" in sig.parameters:
            return func(
                profile=profile,
                daily_stats=daily_stats,
                activities=activities,
                refresh=True,
                weather=_get_weather_from_profile(user_id),
            )
        if "language" in sig.parameters:
            return func(
                profile=profile,
                daily_stats=daily_stats,
                activities=activities,
                refresh=True,
                language=language,
            )
        if "user_id" in sig.parameters:
            return func(profile=profile, daily_stats=daily_stats, activities=activities, refresh=True, user_id=user_id)
    except Exception:
        pass
    return func(profile=profile, daily_stats=daily_stats, activities=activities, refresh=True)


def _get_weather_from_profile(user_id: str) -> dict[str, Any] | None:
    profile = load_user_profile(user_id=user_id) or {}
    lat = profile.get("location_latitude")
    lon = profile.get("location_longitude")
    try:
        latitude = float(lat)
        longitude = float(lon)
    except (TypeError, ValueError):
        return None
    return fetch_current_weather(latitude, longitude)


def _run_for_user(user_id: str, profile: dict[str, Any], due_times: list[str], now: datetime) -> None:
    logger = _get_logger()
    if not due_times:
        return

    ok, message = _reload_garmin_data(user_id)
    if ok:
        logger.info("Auto: Garmin refresh succeeded for user %s.", user_id)
    else:
        logger.warning("Auto: Garmin refresh failed for user %s: %s", user_id, message)

    daily_stats = load_daily_stats(user_id=user_id)
    activities = load_activities(user_id=user_id)

    coach_profile = coach_agent.CoachProfile(
        mobility=str(profile.get("mobility", "Healthy")).strip(),
        preference=str(profile.get("preference", "")).strip(),
        goal=str(profile.get("goal", "Build Strength and Endurance")).strip(),
    )

    try:
        recommendation = _invoke_get_coach_recommendation(coach_profile, daily_stats, activities, user_id)
        notify_result = notify_recommendation(recommendation, profile, daily_stats=daily_stats)
        if notify_result.get("sent"):
            logger.info("Auto: Notification sent for user %s (%s)", user_id, " | ".join(notify_result.get("sent", [])))
        if notify_result.get("errors"):
            logger.warning("Auto: Notification errors for user %s (%s)", user_id, " | ".join(notify_result.get("errors", [])))
    except Exception as exc:
        logger.error("Auto: Recommendation failed for user %s: %s", user_id, exc)

    last_run_map = _get_last_run_map(profile)
    for time_str in due_times:
        last_run_map[time_str] = now.isoformat()
    profile["auto_recommendation_last_run"] = last_run_map
    profile["auto_recommendation_times"] = _normalize_times(profile.get("auto_recommendation_times"))
    save_user_profile(profile, user_id=user_id)


def run_due_auto_recommendations(now: datetime | None = None) -> None:
    if not _RUN_LOCK.acquire(blocking=False):
        return
    try:
        current_time = now or datetime.now()
        for user_id in list_user_ids():
            profile = load_user_profile(user_id=user_id) or {}
            if not profile.get("auto_recommendation_enabled"):
                continue
            times = _normalize_times(profile.get("auto_recommendation_times"))
            if not times:
                continue
            last_run_map = _get_last_run_map(profile)
            due_times = _due_times(current_time, times, last_run_map)
            if due_times:
                _run_for_user(user_id, profile, due_times, current_time)
    finally:
        _RUN_LOCK.release()


def _scheduler_loop(interval_seconds: int) -> None:
    logger = _get_logger()
    while True:
        try:
            run_due_auto_recommendations()
        except Exception as exc:
            logger.error("Auto scheduler error: %s", exc)
        time.sleep(interval_seconds)


def start_scheduler(interval_seconds: int = 60) -> None:
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED:
        return
    _SCHEDULER_STARTED = True
    thread = threading.Thread(target=_scheduler_loop, args=(interval_seconds,), daemon=True)
    thread.start()
