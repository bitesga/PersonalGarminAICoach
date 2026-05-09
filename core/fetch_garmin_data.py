from __future__ import annotations

from datetime import date, datetime, timedelta
import argparse
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from garminconnect import Garmin

from core.data_persistence import (
    load_garmin_credentials,
    save_daily_stats,
    save_activities,
    load_daily_stats,
    load_activities,
    load_garmin_retry_state,
    save_garmin_retry_state,
)

try:
    # Optional specific exceptions from garminconnect.
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )
except ImportError:
    GarminConnectAuthenticationError = Exception
    GarminConnectConnectionError = Exception
    GarminConnectTooManyRequestsError = Exception

logger = logging.getLogger(__name__)


class _GarminLoginLogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        self.messages.append(message)
        lowered = message.lower()
        if "widget+cffi failed" in lowered or "unexpected title 'garmin authentication application'" in lowered:
            raise _AbortGarminLogin(message)


class _AbortGarminLogin(RuntimeError):
    pass


def _looks_like_auth_failure_from_logs(messages: list[str]) -> bool:
    combined = "\n".join(messages).lower()
    return any(
        marker in combined
        for marker in (
            "unexpected title 'garmin authentication application'",
            "login failed",
            "invalid password",
            "incorrect password",
            "authentication required",
        )
    )


def _is_authentication_error_message(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "authentication",
            "auth error",
            "login failed",
            "invalid credential",
            "invalid password",
            "incorrect password",
            "unauthorized",
            "permission denied",
            "401",
            "403",
        )
    )


def _calculate_next_retry_time(retry_count: int) -> datetime:
    """Calculate next retry time with exponential backoff.
    
    Retry schedule:
    - Attempt 0 (first failure): retry in 1 hour
    - Attempt 1 (second failure): retry in 4 hours
    - Attempt 2 (third failure): retry in 24 hours
    - Attempt 3+ (fourth+ failure): retry in 48 hours
    """
    delays = [
        timedelta(hours=1),    # First failure
        timedelta(hours=4),    # Second failure
        timedelta(hours=24),   # Third failure
        timedelta(hours=48),   # Fourth+ failure
    ]
    delay = delays[min(retry_count, len(delays) - 1)]
    next_retry = datetime.now() + delay
    return next_retry


def _should_attempt_garmin_fetch(user_id: str | None = None) -> tuple[bool, dict[str, Any]]:
    """Check if we should attempt Garmin fetch, or if in retry backoff.
    
    Returns:
        (should_attempt, retry_state_dict)
    """
    retry_state = load_garmin_retry_state(user_id=user_id)
    
    if not retry_state:
        # No previous failure, attempt fetch
        logger.debug("No retry state found, attempting Garmin fetch")
        return True, {}

    # Credentials can be fixed by the user at any time; never back off auth failures.
    if str(retry_state.get("last_failure_reason", "")).strip().lower() == "auth_error":
        logger.info("Last failure was auth_error, attempting Garmin fetch without backoff")
        return True, retry_state
    
    next_retry_str = retry_state.get("next_retry_time")
    if not next_retry_str:
        logger.debug("Retry state exists but no next_retry_time, attempting fetch")
        return True, retry_state
    
    try:
        next_retry = datetime.fromisoformat(next_retry_str)
        now = datetime.now()
        
        if now >= next_retry:
            logger.info(f"Retry time reached ({next_retry_str}), attempting Garmin fetch")
            return True, retry_state
        else:
            remaining = next_retry - now
            logger.warning(
                f"Still in retry backoff. Next attempt: {next_retry_str} "
                f"({remaining.total_seconds() / 3600:.1f}h from now)"
            )
            return False, retry_state
    except Exception as e:
        logger.warning(f"Could not parse next_retry_time: {e}, attempting fetch anyway")
        return True, retry_state


def _record_garmin_failure(reason: str, user_id: str | None = None) -> None:
    """Record a Garmin fetch failure and schedule next retry."""
    retry_state = load_garmin_retry_state(user_id=user_id)
    retry_count = retry_state.get("retry_count", 0)
    
    next_retry = _calculate_next_retry_time(retry_count)
    
    updated_state = {
        "retry_count": retry_count + 1,
        "last_failure_time": datetime.now().isoformat(),
        "last_failure_reason": reason,
        "next_retry_time": next_retry.isoformat(),
    }
    
    save_garmin_retry_state(updated_state, user_id=user_id)
    
    hours_until = (next_retry - datetime.now()).total_seconds() / 3600
    logger.warning(
        f"Garmin fetch failed ({reason}). "
        f"Will retry in {hours_until:.1f}h ({next_retry.isoformat()})"
    )


def _clear_garmin_retry_state(user_id: str | None = None) -> None:
    """Clear retry state after successful fetch."""
    save_garmin_retry_state({}, user_id=user_id)
    logger.info("Garmin fetch successful, retry state cleared")


def _get_cached_daily_stats(user_id: str | None = None) -> dict[str, Any]:
    """Get most recent cached daily stats."""
    stats = load_daily_stats(user_id=user_id)
    if stats:
        # Find most recent date
        most_recent_date = max(stats.keys()) if stats else None
        if most_recent_date:
            logger.warning(f"Using cached daily stats from {most_recent_date}")
            return stats
    return {}



def _get_nested(data: Any, path: list[str], default: Any = None) -> Any:
    """Safely read nested dictionary values."""
    current = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _extract_body_battery(stats: dict[str, Any]) -> Any:
    candidates = [
        ["bodyBattery", "mostRecentValue"],
        ["bodyBattery", "endingBodyBattery"],
        ["bodyBatteryMostRecentValue"],
        ["bodyBattery", "bodyBatteryValuesArray", "latest"],
    ]
    for path in candidates:
        value = _get_nested(stats, path)
        if value is not None:
            return value
    return "N/A"


def _extract_sleep_score(stats: dict[str, Any]) -> Any:
    def _is_valid_sleep_score(value: Any) -> bool:
        return isinstance(value, (int, float)) and 1 <= float(value) <= 100

    def _normalize_score(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            for key in ("value", "score", "overallScore", "sleepScore"):
                if value.get(key) is not None:
                    return value.get(key)
            return None
        return value

    candidates = [
        # Garmin's get_sleep_data payload structure (most reliable, explicit daily score).
        ["dailySleepDTO", "sleepScores", "overall", "value"],
        ["dailySleepDTO", "sleepScores", "overallScore", "value"],
        ["dailySleepDTO", "sleepScores", "overall"],
        ["dailySleepDTO", "sleepScores", "overallScore"],
        # Fallback to get_stats payload structure.
        ["sleepScores", "overallScore", "value"],
        ["sleepScores", "overallScore"],
        ["sleepScores", "value"],
        ["dailySleepDTO", "sleepScore"],
        ["sleep", "sleepScore"],
        ["sleepScore"],
    ]
    for path in candidates:
        value = _normalize_score(_get_nested(stats, path))
        if _is_valid_sleep_score(value):
            return value

    # Fallback: recursively find a numeric score in keys containing both sleep and score.
    def _walk(node: Any, key_path: str = "") -> Any:
        if isinstance(node, dict):
            for key, value in node.items():
                next_path = f"{key_path}.{key}" if key_path else str(key)
                if (
                    isinstance(value, (int, float))
                    and "sleep" in next_path.lower()
                    and "score" in next_path.lower()
                    and _is_valid_sleep_score(value)
                ):
                    return value
                found = _walk(value, next_path)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                found = _walk(item, f"{key_path}[{idx}]")
                if found is not None:
                    return found
        return None

    found_score = _walk(stats)
    if found_score is not None:
        return found_score

    return "N/A"


def _call_with_backoff(func: Any, *args: Any, retries: int = 3, base_delay: float = 2.0) -> Any:
    """Retry API calls on Garmin rate limits with exponential backoff."""
    for attempt in range(retries + 1):
        try:
            return func(*args)
        except GarminConnectTooManyRequestsError:
            if attempt >= retries:
                raise
            delay = base_delay * (2 ** attempt)
            print(
                f"Rate-Limit erkannt. Neuer Versuch in {delay:.0f}s ({attempt + 1}/{retries}).",
                file=sys.stderr,
            )
            time.sleep(delay)


def _extract_activity_data(activity: dict[str, Any]) -> tuple[str, str]:
    activity_type = (
        _get_nested(activity, ["activityType", "typeKey"]) 
        or _get_nested(activity, ["activityType", "typeId"]) 
        or activity.get("activityName")
        or "N/A"
    )
    
    activity_type_key = str(activity_type).lower()
    
    # For strength training / weightlifting, extract exercise sets (movements performed)
    if "strength" in activity_type_key or "weight" in activity_type_key:
        exercise_sets = activity.get("summarizedExerciseSets") or []
        
        # Extract unique exercise categories
        exercises = []
        for exercise_set in exercise_sets:
            category = exercise_set.get("category") or "Unknown"
            exercises.append(str(category))
        
        # Return as comma-separated list or fallback to sets count
        if exercises:
            return str(activity_type), ", ".join(exercises)
        else:
            total_sets = activity.get("totalSets") or "N/A"
            return str(activity_type), f"{total_sets} sets"
    
    # For cardio activities, extract training effect
    training_effect = (
        activity.get("trainingEffect")
        or activity.get("aerobicTrainingEffect")
        or activity.get("anaerobicTrainingEffect")
        or _get_nested(activity, ["trainingEffectLabel"])
        or "N/A"
    )

    return str(activity_type), str(training_effect)


def _extract_stress(stats: dict[str, Any]) -> Any:
    """Extract average stress value from daily stats."""
    candidates = [
        ["averageStressLevel"],  # Primary: direct daily average stress
        ["stress", "average"],
        ["stressValues", "average"],
        ["stressAverage"],
        ["averageStress"],
    ]
    for path in candidates:
        value = _get_nested(stats, path)
        if value is not None and isinstance(value, (int, float)):
            return round(float(value), 1)
    return "N/A"


def _extract_resting_heart_rate(stats: dict[str, Any]) -> Any:
    """Extract resting heart rate from daily stats."""
    candidates = [
        ["restingHeartRate"],
        ["restingHeartRateValue"],
        ["rhr"],
        ["heartRate", "restingHeartRate"],
    ]
    for path in candidates:
        value = _get_nested(stats, path)
        if value is not None and isinstance(value, (int, float)):
            return int(value)
    return "N/A"


def _extract_training_load(data: dict[str, Any]) -> Any:
    """Extract acute training load (dailyTrainingLoadAcute) from training status."""
    # Path: mostRecentTrainingStatus.latestTrainingStatusData[deviceId].acuteTrainingLoadDTO.dailyTrainingLoadAcute
    try:
        latest_training_status = _get_nested(data, ["mostRecentTrainingStatus", "latestTrainingStatusData"])
        if isinstance(latest_training_status, dict):
            # Get first device in the map
            for device_id, device_data in latest_training_status.items():
                daily_load = _get_nested(device_data, ["acuteTrainingLoadDTO", "dailyTrainingLoadAcute"])
                if isinstance(daily_load, (int, float)):
                    return round(float(daily_load), 1)
    except Exception:
        pass
    return "N/A"


def _extract_training_balance_feedback(data: dict[str, Any]) -> Any:
    """Extract training balance feedback phrase (e.g., AEROBIC_HIGH_SHORTAGE)."""
    # Path: mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap[deviceId].trainingBalanceFeedbackPhrase
    try:
        load_balance = _get_nested(data, ["mostRecentTrainingLoadBalance", "metricsTrainingLoadBalanceDTOMap"])
        if isinstance(load_balance, dict):
            # Get first device in the map
            for device_id, device_data in load_balance.items():
                feedback = device_data.get("trainingBalanceFeedbackPhrase")
                if feedback:
                    return str(feedback)
    except Exception:
        pass
    return "N/A"


def _extract_vo2max_from_profile(profile: dict[str, Any]) -> Any:
    """Extract VO2Max from user profile (supports multiple sports)."""
    # Check in priority order: running > cycling > swimming > any vo2Max field
    candidates = [
        ["userData", "vo2MaxRunning"],
        ["userData", "vo2MaxCycling"],
        ["userData", "vo2MaxSwimming"],
        ["vo2MaxRunning"],
        ["vo2MaxCycling"],
        ["vo2MaxSwimming"],
    ]
    
    for path in candidates:
        value = _get_nested(profile, path)
        if value is not None and isinstance(value, (int, float)) and 15 <= float(value) <= 90:
            return round(float(value), 2)
    
    return "N/A"


def _format_activity_time(activity: dict[str, Any]) -> str:
    """Return a HH:MM activity time if Garmin provides a start timestamp."""
    start_time = activity.get("startTimeInSeconds")
    if start_time:
        try:
            return datetime.fromtimestamp(start_time).strftime("%H:%M")
        except (ValueError, TypeError, OSError):
            pass
    start_time_gmt = activity.get("startTimeGMT")
    if start_time_gmt:
        start_text = str(start_time_gmt)
        if "T" in start_text:
            return start_text.split("T", 1)[1][:5]
        if " " in start_text:
            return start_text.split(" ", 1)[1][:5]
        return start_text[:5]
    return "N/A"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Garmin data for a specific user.")
    parser.add_argument("--user-id", dest="user_id", default="", help="Discord user ID for scoped storage")
    parser.add_argument("--debug", dest="debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--force", dest="force", action="store_true", help="Ignore retry backoff and fetch Garmin data anyway")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )
    
    user_id = str(args.user_id).strip()
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path)
    
    logger.info(f"Starting Garmin data fetch for user_id={user_id}")

    logger.debug("Loading Garmin credentials...")
    stored_credentials = load_garmin_credentials(user_id=user_id) if user_id else {}
    email = stored_credentials.get("email") or os.getenv("GARMIN_EMAIL")
    password = stored_credentials.get("password") or os.getenv("GARMIN_PASSWORD")
    
    cred_source = "stored" if stored_credentials else "env"
    logger.info(f"Using credentials from: {cred_source}")
    if email:
        logger.debug(f"Email: {email}")

    if not email or not password:
        logger.error(
            "Missing credentials. Please add Garmin email and password in the app "
            "or set GARMIN_EMAIL and GARMIN_PASSWORD in the .env file."
        )
        return 1

    # ===== Check if we should attempt Garmin fetch or use cached data =====
    should_attempt, retry_state = _should_attempt_garmin_fetch(user_id=user_id) if not args.force else (True, {})
    if args.force:
        logger.warning("Force mode enabled. Ignoring retry backoff and attempting Garmin fetch now.")
    
    if not should_attempt:
        logger.warning("Skipping Garmin fetch due to rate-limit backoff. Using cached data.")
        # Load and return cached data
        cached_stats = _get_cached_daily_stats(user_id=user_id)
        cached_activities = load_activities(user_id=user_id)
        if cached_stats or cached_activities:
            if cached_stats:
                logger.info(f"Saving {len(cached_stats)} cached daily stats")
                save_daily_stats(cached_stats, user_id=user_id or None)
            if cached_activities:
                logger.info(f"Saving {len(cached_activities)} cached activities")
                save_activities(cached_activities, user_id=user_id or None)
            logger.info("Garmin data fetch completed (using cache)")
            return 0
        else:
            logger.warning("No cached data available. Attempt skipped.")
            return 1

    logger.info("Attempting Garmin login...")
    garmin_logger = logging.getLogger("garminconnect.client")
    login_capture = _GarminLoginLogCapture()
    login_capture.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    garmin_logger.addHandler(login_capture)
    try:
        client = Garmin(email=email, password=password)
        logger.debug(f"Garmin client created, calling login()...")
        client.login()
        if _looks_like_auth_failure_from_logs(login_capture.messages):
            logger.error("AUTH_ERROR: Garmin sign-in page indicated invalid credentials.")
            _record_garmin_failure("auth_error", user_id=user_id)
            return 1
        logger.info("Garmin login successful")
    except _AbortGarminLogin as e:
        logger.error(f"AUTH_ERROR: Garmin login stopped after widget auth failure. {e}")
        _record_garmin_failure("auth_error", user_id=user_id)
        return 1
    except GarminConnectAuthenticationError as e:
        logger.error(f"AUTH_ERROR: Login failed: please check your email/password. {e}")
        _record_garmin_failure("auth_error", user_id=user_id)
        return 1
    except GarminConnectConnectionError as e:
        error_msg = str(e)
        if _is_authentication_error_message(error_msg):
            logger.error(f"AUTH_ERROR: Garmin login failed. Please check your email/password. {e}")
            _record_garmin_failure("auth_error", user_id=user_id)
            return 1
        if "429" in error_msg or "rate limit" in error_msg.lower():
            logger.error(f"RATE_LIMIT: Rate limit error from Garmin: {e}")
            _record_garmin_failure("rate_limit_429", user_id=user_id)
        elif "captcha" in error_msg.lower():
            logger.error(f"CAPTCHA_REQUIRED: CAPTCHA required by Garmin: {e}")
            _record_garmin_failure("captcha_required", user_id=user_id)
        else:
            logger.error(f"Connection error during Garmin login: {e}")
            _record_garmin_failure("connection_error", user_id=user_id)
        # Try to use cached data
        cached_stats = _get_cached_daily_stats(user_id=user_id)
        cached_activities = load_activities(user_id=user_id)
        if cached_stats or cached_activities:
            if cached_stats:
                save_daily_stats(cached_stats, user_id=user_id or None)
            if cached_activities:
                save_activities(cached_activities, user_id=user_id or None)
            logger.info("Saved cached data, returning 0 (partial success)")
            return 0
        return 1
    except GarminConnectTooManyRequestsError as e:
        logger.error(f"Too many requests to Garmin: {e}")
        _record_garmin_failure("too_many_requests", user_id=user_id)
        return 1
    except Exception as exc:
        logger.error(f"Unexpected login error: {type(exc).__name__}: {exc}")
        logger.debug(f"Full traceback:", exc_info=True)
        _record_garmin_failure(f"unexpected_{type(exc).__name__}", user_id=user_id)
        return 1
    finally:
        garmin_logger.removeHandler(login_capture)

    today = date.today()
    today_iso = today.isoformat()
    logger.info(f"Today's date: {today_iso}")

    # ===== Fetch user profile (for potential VO2Max and other profile metrics) =====
    logger.debug("Fetching user profile...")
    try:
        user_profile = _call_with_backoff(client.get_user_profile)
        logger.debug(f"User profile fetched successfully")
    except Exception as e:
        logger.warning(f"Could not fetch profile: {e}")
        user_profile = {}

    # ===== Fetch training load metrics =====
    logger.debug("Fetching training load metrics...")
    try:
        training_status = _call_with_backoff(client.get_training_status, today_iso)
        training_load = _extract_training_load(training_status)
        training_balance_feedback = _extract_training_balance_feedback(training_status)
        logger.debug(f"Training load: {training_load}, feedback: {training_balance_feedback}")
    except Exception as e:
        logger.warning(f"Could not fetch training load: {e}")
        training_load = "N/A"
        training_balance_feedback = "N/A"

    # ===== Fetch latest activity data =====
    logger.debug("Fetching activities...")
    activities_all = _call_with_backoff(client.get_activities, 0, 7)
    logger.info(f"Fetched {len(activities_all)} activities from Garmin")
    activities_to_save = []
    
    for idx, activity in enumerate(activities_all[:7]):
        activity_type, training_effect = _extract_activity_data(activity)
        activity_id = activity.get("activityId") or activity.get("activityIdKey") or f"garmin-{idx}-{activity.get('startTimeInSeconds') or activity.get('startTimeGMT') or today_iso}"
        
        # Convert startTimeInSeconds (Unix timestamp) to ISO date string
        start_time = activity.get("startTimeInSeconds")
        if start_time:
            try:
                activity_date = datetime.fromtimestamp(start_time).isoformat()
            except (ValueError, TypeError):
                activity_date = "N/A"
        else:
            activity_date = activity.get("startTimeGMT") or "N/A"
        
        entry: dict[str, Any] = {
            "id": str(activity_id),
            "index": idx,
            "date": activity_date,
            "time": _format_activity_time(activity),
            "activity_type": activity_type,
            "primary_metric": training_effect,  # Training Effect for cardio, Exercises for strength
            "duration": activity.get("duration") or "N/A",
            "calories": activity.get("calories") or "N/A",
            "source": "garmin",
        }

        # Omit distance for strength training (not meaningful)
        if not ("strength" in str(activity_type).lower() or "weight" in str(activity_type).lower()):
            entry["distance"] = activity.get("distance") or "N/A"

        activities_to_save.append(entry)

    latest_activity = activities_all[0] if activities_all else {}
    activity_type, training_effect = _extract_activity_data(latest_activity)

    # Extract VO2Max from user profile (supports running, cycling, swimming, etc.)
    vo2max_from_profile = _extract_vo2max_from_profile(user_profile)

    daily_stats_data: dict[str, Any] = {}
    for day_offset in range(7):
        target_date = (today - timedelta(days=day_offset)).isoformat()
        try:
            stats = _call_with_backoff(client.get_stats, target_date)
            body_battery = _extract_body_battery(stats)
            
            stress = _extract_stress(stats)
            # Use VO2Max from user profile (supports running, cycling, swimming, etc.)
            vo2max = vo2max_from_profile
            rhr = _extract_resting_heart_rate(stats)
            
            
            sleep_score = _extract_sleep_score(stats)
            if sleep_score == "N/A":
                try:
                    sleep_data = _call_with_backoff(client.get_sleep_data, target_date)
                    sleep_score = _extract_sleep_score(sleep_data)
                    pass
                except Exception:
                    pass

            daily_stats_data[target_date] = {
                "date": target_date,
                "body_battery": body_battery,
                "sleep_score": sleep_score,
                "stress": stress,
                "vo2_max": vo2max,
                "resting_heart_rate": rhr,
                "training_load": training_load if day_offset == 0 else "N/A",
                "training_load_acute": training_load if day_offset == 0 else "N/A",
                "training_balance_feedback": training_balance_feedback if day_offset == 0 else "N/A",
            }
            
            if day_offset == 0:
                logger.info(f"--- Garmin daily summary ({target_date}) ---")
                logger.info(f"Activity type: {activity_type}")
                
                # Label for activity_type based metric
                activity_type_key = str(activity_type).lower()
                if "strength" in activity_type_key or "weight" in activity_type_key:
                    logger.info(f"Exercises: {training_effect}")
                else:
                    logger.info(f"Training Effect: {training_effect}")
                
                logger.info(f"Body Battery: {body_battery}")
                logger.info(f"Sleep Score: {sleep_score}")
                logger.info(f"Stress (average): {stress}")
                logger.info(f"VO2 Max: {vo2max}")
                logger.info(f"Resting HR: {rhr}")
                logger.info(f"Training load: {training_load}")
                logger.info(f"Training load (acute): {training_load}")
                logger.info(f"Training Balance: {training_balance_feedback}")
        except Exception as exc:
            logger.error(f"Error fetching stats for {target_date}: {exc}")
            pass


    # ===== Save to JSON =====
    try:
        stats_file = save_daily_stats(daily_stats_data, user_id=user_id or None)
        logger.info(f"Daily stats saved to: {stats_file}")
    except Exception as exc:
        logger.error(f"Error saving daily stats: {exc}")

    try:
        activities_file = save_activities(activities_to_save, user_id=user_id or None)
        logger.info(f"Activities saved to: {activities_file}")
    except Exception as exc:
        logger.error(f"Error saving activities: {exc}")

    # Clear retry state on successful fetch
    _clear_garmin_retry_state(user_id=user_id)
    logger.info("Garmin data fetch completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
