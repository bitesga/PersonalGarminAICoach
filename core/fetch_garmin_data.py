from __future__ import annotations

from datetime import date, datetime, timedelta
import argparse
import os
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import load_dotenv
from garminconnect import Garmin

from data_persistence import load_garmin_credentials, save_daily_stats, save_activities

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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    user_id = str(args.user_id).strip()
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path)

    stored_credentials = load_garmin_credentials(user_id=user_id) if user_id else {}
    email = stored_credentials.get("email") or os.getenv("GARMIN_EMAIL")
    password = stored_credentials.get("password") or os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        print(
            (
                "Missing credentials. Please add Garmin email and password in the app "
                "or set GARMIN_EMAIL and GARMIN_PASSWORD in the .env file."
            ),
            file=sys.stderr,
        )
        return 1

    try:
        client = Garmin(email=email, password=password)
        client.login()
    except GarminConnectAuthenticationError:
        print("Login failed: please check your email/password.", file=sys.stderr)
        return 1
    except GarminConnectConnectionError:
        print("Connection error during Garmin login. Please try again later.", file=sys.stderr)
        return 1
    except GarminConnectTooManyRequestsError:
        print("Too many requests to Garmin. Please wait briefly and try again.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected login error: {exc}", file=sys.stderr)
        return 1

    today = date.today()
    today_iso = today.isoformat()

    # ===== Fetch user profile (for potential VO2Max and other profile metrics) =====
    try:
        user_profile = _call_with_backoff(client.get_user_profile)
    except Exception as e:
        print(f"Warning: could not fetch profile: {e}", file=sys.stderr)
        user_profile = {}

    # ===== Fetch training load metrics =====
    try:
        training_status = _call_with_backoff(client.get_training_status, today_iso)
        training_load = _extract_training_load(training_status)
        training_balance_feedback = _extract_training_balance_feedback(training_status)
    except Exception as e:
        print(f"Warning: could not fetch training load: {e}", file=sys.stderr)
        training_load = "N/A"
        training_balance_feedback = "N/A"

    # ===== Fetch latest activity data =====
    activities_all = _call_with_backoff(client.get_activities, 0, 7)
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
                print(f"--- Garmin daily summary ({target_date}) ---")
                print(f"Activity type: {activity_type}")
                
                # Label for activity_type based metric
                activity_type_key = str(activity_type).lower()
                if "strength" in activity_type_key or "weight" in activity_type_key:
                    print(f"Exercises: {training_effect}")
                else:
                    print(f"Training Effect: {training_effect}")
                
                print(f"Body Battery: {body_battery}")
                print(f"Sleep Score: {sleep_score}")
                print(f"Stress (average): {stress}")
                print(f"VO2 Max: {vo2max}")
                print(f"Resting HR: {rhr}")
                print(f"Training load: {training_load}")
                print(f"Training load (acute): {training_load}")
                print(f"Training Balance: {training_balance_feedback}")
                print()
        except Exception as exc:
            print(f"Error fetching stats for {target_date}: {exc}", file=sys.stderr)
            pass


    # ===== Save to JSON =====
    try:
        stats_file = save_daily_stats(daily_stats_data, user_id=user_id or None)
        print(f"Daily stats saved to: {stats_file}")
    except Exception as exc:
        print(f"Error saving daily stats: {exc}", file=sys.stderr)

    try:
        activities_file = save_activities(activities_to_save, user_id=user_id or None)
        print(f"Activities saved to: {activities_file}")
    except Exception as exc:
        print(f"Error saving activities: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
