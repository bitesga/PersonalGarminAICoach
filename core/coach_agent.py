"""Build and run an adaptive coach prompt from Garmin JSON data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_MODEL_NAME = "gemini-2.5-flash"
CACHE_PATH = DATA_DIR / "coach_recommendation.json"
CACHE_TTL_HOURS = 6

@dataclass(frozen=True)
class CoachProfile:
    """User context that shapes the recommendation."""

    mobility: str
    preference: str
    goal: str


COACH_SYSTEM_PROMPT = (
    "Du bist ein adaptive Fitness-Coach. Antworte nur als kompaktes JSON mit den Keys "
    "titel, empfehlung, intensitaet, begruendung. Halte die Antwort sehr kurz und konkret."
)


def _load_environment() -> None:
    load_dotenv(dotenv_path=ENV_PATH)


def _load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def load_coach_inputs(data_dir: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base_dir = data_dir or DATA_DIR
    daily_stats = _load_json_file(base_dir / "daily_stats.json", {})
    activities_payload = _load_json_file(base_dir / "activities.json", {"activities": []})
    activities = activities_payload.get("activities", []) if isinstance(activities_payload, dict) else []
    return daily_stats, activities


def _load_cached_recommendation(cache_path: Path = CACHE_PATH) -> dict[str, Any] | None:
    payload = _load_json_file(cache_path, {})
    if not isinstance(payload, dict):
        return None

    generated_at = payload.get("generated_at")
    recommendation = payload.get("recommendation")
    if not generated_at or not isinstance(recommendation, dict):
        return None

    try:
        generated_dt = datetime.fromisoformat(str(generated_at))
    except ValueError:
        return None

    age_hours = (datetime.now() - generated_dt).total_seconds() / 3600
    if age_hours >= CACHE_TTL_HOURS:
        return None

    cached = dict(recommendation)
    cached["source"] = "cache"
    cached["cached_at"] = generated_dt.isoformat()
    cached["cache_age_hours"] = round(age_hours, 2)
    return cached


def _save_cached_recommendation(recommendation: dict[str, Any], cache_path: Path = CACHE_PATH) -> None:
    cache_path.parent.mkdir(exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "recommendation": recommendation,
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _compact_daily_stats(daily_stats: dict[str, Any]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for date_key in sorted(daily_stats.keys(), reverse=True):
        day = daily_stats.get(date_key, {})
        if not isinstance(day, dict):
            continue
        compact.append(
            {
                "date": day.get("date", date_key),
                "sleep_score": day.get("sleep_score"),
                "body_battery": day.get("body_battery"),
                "stress": day.get("stress"),
                "vo2_max": day.get("vo2_max"),
                "resting_heart_rate": day.get("resting_heart_rate"),
            }
        )
    return compact[:7]


def _compact_activities(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for activity in activities[:7]:
        if not isinstance(activity, dict):
            continue
        compact.append(
            {
                "date": activity.get("date"),
                "activity_type": activity.get("activity_type"),
                "primary_metric": activity.get("primary_metric"),
                "duration": activity.get("duration"),
                "calories": activity.get("calories"),
                "distance": activity.get("distance"),
            }
        )
    return compact


def build_coach_prompt(profile: CoachProfile, daily_stats: dict[str, Any], activities: list[dict[str, Any]]) -> str:
    user_payload = {
        "nutzerprofil": {
            "mobilitaet": profile.mobility,
            "praeferenz": profile.preference,
            "ziel": profile.goal,
        },
        "historie_7_tage": _compact_daily_stats(daily_stats),
        "letzte_aktivitaeten": _compact_activities(activities),
        "ausgabeformat": {
            "titel": "...",
            "empfehlung": "...",
            "intensitaet": 1,
            "begruendung": "...",
        },
        "regeln": [
            "Wenn Schlaf < 60 oder Body Battery < 40, dann Ruhetag oder sehr leichte Mobilität.",
            "Keine Lauf-Intervalle fuer Rollstuhlfahrer; stattdessen Handbike oder Oberkoerper-Kraft-Ausdauer.",
            "Outdoor bevorzugen, wenn die Erholung nicht kritisch ist.",
        ],
    }
    return (
        f"{COACH_SYSTEM_PROMPT}\n"
        f"{json.dumps(user_payload, ensure_ascii=False, separators=(',', ':'), default=str)}\n"
        "Antwort nur als JSON ohne Markdown."
    )


def format_coach_message(recommendation: dict[str, Any]) -> str:
    title = str(recommendation.get("titel", "Empfehlung"))
    recommendation_text = str(recommendation.get("empfehlung", "Keine Empfehlung verfuegbar."))
    intensity = recommendation.get("intensitaet", "n/a")
    reasoning = str(recommendation.get("begruendung", ""))
    source = str(recommendation.get("source", "model"))

    lines = [
        f"**{title}**",
        f"Intensität: {intensity}/10",
        f"Empfehlung: {recommendation_text}",
        f"Begründung: {reasoning}",
        f"Quelle: {source}",
    ]

    cached_at = recommendation.get("cached_at")
    cache_age_hours = recommendation.get("cache_age_hours")
    if cached_at and cache_age_hours is not None:
        lines.append(f"Cache: zuletzt aktualisiert um {cached_at} ({cache_age_hours}h alt)")

    return "\n".join(lines)


class GeminiCoachClient:
    def __init__(self, api_key: str, model_name: str = DEFAULT_MODEL_NAME) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name

    def generate_content(self, prompt: str) -> Any:
        return self._client.models.generate_content(
            model=self._model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "Antworte ausschließlich als minimales JSON mit den Schlüsseln titel, empfehlung, intensitaet, begruendung. "
                    "Keine Markdown-Formatierung, kein Zusatztext. Halte die Antwort kurz und konkret."
                ),
                temperature=0.2,
                top_p=0.8,
                top_k=32,
                max_output_tokens=220,
                response_mime_type="application/json",
            ),
        )


def _extract_json_response(response_text: str) -> dict[str, Any]:
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        raise


def _as_number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_stat_day(daily_stats: dict[str, Any]) -> dict[str, Any]:
    if not daily_stats:
        return {}
    latest_key = sorted(daily_stats.keys())[-1]
    latest_day = daily_stats.get(latest_key, {})
    return latest_day if isinstance(latest_day, dict) else {}


def _local_recommendation(profile: CoachProfile, daily_stats: dict[str, Any], activities: list[dict[str, Any]]) -> dict[str, Any]:
    latest_day = _latest_stat_day(daily_stats)
    sleep_score = _as_number(latest_day.get("sleep_score"))
    body_battery = _as_number(latest_day.get("body_battery"))
    stress = _as_number(latest_day.get("stress"))

    mobility = profile.mobility.lower()
    preference = profile.preference.lower()

    recovery_is_low = (
        (sleep_score is not None and sleep_score < 60)
        or (body_battery is not None and body_battery < 40)
    )

    if recovery_is_low:
        return {
            "titel": "Regeneration",
            "empfehlung": "Ruhetag oder 20-30 Minuten sehr leichte Mobilität und lockeres Spazieren/Ergometer ohne Druck.",
            "intensitaet": 1,
            "begruendung": f"Schlaf-Score {sleep_score if sleep_score is not None else 'n/a'} und Body Battery {body_battery if body_battery is not None else 'n/a'} sprechen fuer Erholung.",
        }

    if "rollstuhl" in mobility or "wheelchair" in mobility:
        return {
            "titel": "Oberkoerper-Intervall",
            "empfehlung": "Handbike-Intervalle oder Kraft-Ausdauer fuer Brust, Ruecken und Schulterguertel.",
            "intensitaet": 7,
            "begruendung": "Mobilitaet ist eingeschraenkt auf Rollstuhl-Training; Lauf-Intervalle sind deshalb nicht passend.",
        }

    if "drau" in preference or "outdoor" in preference:
        return {
            "titel": "Outdoor-Ausdauer",
            "empfehlung": "Eine lockere bis zuegige Outdoor-Einheit mit Lauf- oder Radanteil, alternativ kurze Tempoabschnitte.",
            "intensitaet": 6,
            "begruendung": f"Die Erholung sieht stabil aus; Outdoor passt zur Praeferenz. Stress liegt bei {stress if stress is not None else 'n/a'}.",
        }

    latest_activity_type = str((activities[0] if activities else {}).get("activity_type", "")).lower()
    if "strength" in latest_activity_type:
        return {
            "titel": "Kraft-Fokus",
            "empfehlung": "Krafttraining mit sauberer Technik und moderater Last, danach lockeres Auslaufen oder Mobility.",
            "intensitaet": 6,
            "begruendung": "Die letzten Daten zeigen keine klare Ermuedung, daher ist eine strukturierte Kraft-Einheit sinnvoll.",
        }

    return {
        "titel": "Ausgewogene Einheit",
        "empfehlung": "Moderates Ausdauertraining oder Ganzkoerper-Krafttraining mit sauberer Technik.",
        "intensitaet": 5,
        "begruendung": "Die Erholungswerte sind ausreichend und es gibt keine harte Restriktion durch Mobilitaet oder Praeferenz.",
    }


def generate_coach_recommendation(
    profile: CoachProfile,
    client: Any,
    daily_stats: dict[str, Any] | None = None,
    activities: list[dict[str, Any]] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    if daily_stats is None or activities is None:
        loaded_stats, loaded_activities = load_coach_inputs()
        daily_stats = daily_stats or loaded_stats
        activities = activities or loaded_activities

    if not refresh:
        cached_recommendation = _load_cached_recommendation()
        if cached_recommendation is not None:
            return cached_recommendation

    prompt = build_coach_prompt(profile, daily_stats, activities)
    try:
        response = client.generate_content(prompt)
        response_text = getattr(response, "text", str(response))
        recommendation = _extract_json_response(response_text)
        recommendation["source"] = "model"
        _save_cached_recommendation(recommendation)
        return recommendation
    except Exception:
        recommendation = _local_recommendation(profile, daily_stats, activities)
        recommendation["source"] = "local"
        _save_cached_recommendation(recommendation)
        return recommendation


def get_coach_recommendation(
    profile: CoachProfile,
    daily_stats: dict[str, Any] | None = None,
    activities: list[dict[str, Any]] | None = None,
    refresh: bool = False,
    model_name: str = DEFAULT_MODEL_NAME,
) -> dict[str, Any]:
    if daily_stats is None or activities is None:
        loaded_stats, loaded_activities = load_coach_inputs()
        daily_stats = daily_stats or loaded_stats
        activities = activities or loaded_activities

    if not refresh:
        cached_recommendation = _load_cached_recommendation()
        if cached_recommendation is not None:
            return cached_recommendation

    client = _build_client(model_name)
    if client is None:
        recommendation = _local_recommendation(profile, daily_stats, activities)
        recommendation["source"] = "local"
        _save_cached_recommendation(recommendation)
        return recommendation

    recommendation = generate_coach_recommendation(profile, client, daily_stats, activities, refresh=refresh)
    _save_cached_recommendation(recommendation)
    return recommendation


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or run the Garmin coach prompt.")
    parser.add_argument("--mobility", default="Läufer", help="Mobilitätsprofil, z. B. Rollstuhlfahrer oder Läufer")
    parser.add_argument("--preference", default="Trainiert gerne draußen", help="Trainingspräferenz")
    parser.add_argument("--goal", default="Maximale Kraft und Ausdauer-Erhalt", help="Trainingsziel")
    parser.add_argument("--run-model", action="store_true", help="Direkt Gemini ansprechen")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="Gemini-Modellname")
    parser.add_argument("--refresh", action="store_true", help="Cache ignorieren und neue Empfehlung anfordern")
    return parser.parse_args()


def _build_client(model_name: str) -> GeminiCoachClient | None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    return GeminiCoachClient(api_key=api_key, model_name=model_name)


def main() -> int:
    _load_environment()
    args = _parse_args()
    profile = CoachProfile(mobility=args.mobility, preference=args.preference, goal=args.goal)
    daily_stats, activities = load_coach_inputs()

    if not args.run_model:
        print(build_coach_prompt(profile, daily_stats, activities))
        return 0

    recommendation = get_coach_recommendation(
        profile=profile,
        daily_stats=daily_stats,
        activities=activities,
        refresh=args.refresh,
        model_name=args.model,
    )
    print(format_coach_message(recommendation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())