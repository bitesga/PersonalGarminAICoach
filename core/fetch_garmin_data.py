from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import load_dotenv
from garminconnect import Garmin

from data_persistence import save_daily_stats, save_activities

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
        ["sleepScores", "overall", "value"],
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


def main() -> int:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path)

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        print(
            (
                "Fehlende Zugangsdaten. Bitte setze GARMIN_EMAIL und GARMIN_PASSWORD "
                f"in {env_path}."
            ),
            file=sys.stderr,
        )
        return 1

    try:
        client = Garmin(email=email, password=password)
        client.login()
    except GarminConnectAuthenticationError:
        print("Login fehlgeschlagen: Bitte E-Mail/Passwort pruefen.", file=sys.stderr)
        return 1
    except GarminConnectConnectionError:
        print("Verbindungsfehler beim Garmin-Login. Bitte spaeter erneut versuchen.", file=sys.stderr)
        return 1
    except GarminConnectTooManyRequestsError:
        print("Zu viele Anfragen an Garmin. Bitte kurz warten und erneut versuchen.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unerwarteter Fehler beim Login: {exc}", file=sys.stderr)
        return 1

    today = date.today()
    today_iso = today.isoformat()

    # ===== Fetch user profile (for potential VO2Max and other profile metrics) =====
    try:
        user_profile = _call_with_backoff(client.get_user_profile)
    except Exception as e:
        print(f"Warnung: Konnte Profil nicht abrufen: {e}", file=sys.stderr)
        user_profile = {}

    # ===== Fetch latest activity data =====
    activities_all = _call_with_backoff(client.get_activities, 0, 7)
    activities_to_save = []
    
    for idx, activity in enumerate(activities_all[:7]):
        activity_type, training_effect = _extract_activity_data(activity)
        
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
            "index": idx,
            "date": activity_date,
            "activity_type": activity_type,
            "primary_metric": training_effect,  # Training Effect for cardio, Exercises for strength
            "duration": activity.get("duration") or "N/A",
            "calories": activity.get("calories") or "N/A",
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
            }
            
            if day_offset == 0:
                print(f"--- Garmin Tageszusammenfassung ({target_date}) ---")
                print(f"Aktivitaetstyp: {activity_type}")
                
                # Label for activity_type based metric
                activity_type_key = str(activity_type).lower()
                if "strength" in activity_type_key or "weight" in activity_type_key:
                    print(f"Übungen: {training_effect}")
                else:
                    print(f"Training Effect: {training_effect}")
                
                print(f"Body Battery: {body_battery}")
                print(f"Sleep Score: {sleep_score}")
                print(f"Stress (Durchschnitt): {stress}")
                print(f"VO2 Max: {vo2max}")
                print(f"Ruhepuls: {rhr}")
                print()
        except Exception as exc:
            print(f"Fehler beim Abrufen der Stats für {target_date}: {exc}", file=sys.stderr)
            pass


    # ===== Save to JSON =====
    try:
        stats_file = save_daily_stats(daily_stats_data)
        print(f"Daily Stats gespeichert in: {stats_file}")
    except Exception as exc:
        print(f"Fehler beim Speichern der Daily Stats: {exc}", file=sys.stderr)

    try:
        activities_file = save_activities(activities_to_save)
        print(f"Aktivitaeten gespeichert in: {activities_file}")
    except Exception as exc:
        print(f"Fehler beim Speichern der Aktivitaeten: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
