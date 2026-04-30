"""Build and run an adaptive coach prompt from Garmin JSON data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from groq import Groq


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_GROQ_MODEL_NAME = "llama-3.3-70b-versatile"
CACHE_PATH = DATA_DIR / "coach_recommendation.json"
CACHE_TTL_HOURS = 6

@dataclass(frozen=True)
class CoachProfile:
    """User context that shapes the recommendation."""

    mobility: str
    preference: str
    goal: str


COACH_SYSTEM_PROMPT = (
    "Du bist ein präziser Fitness-Coach mit ABSOLUTER PRIORITÄT auf Überlastungsschutz. "
    "Antworte AUSSCHLIESSLICH als JSON mit den Keys titel, empfehlung, intensitaet, begruendung. "
    "Die Empfehlung MUSS genau die naechste konkrete Einheit oder maximal die naechsten 1-2 Einheiten beschreiben (Dauer, Ablauf, Intensität). "
    "Keine Wochenplanung, keine Routinen, keine Frequenzangaben wie '2 Mal pro Woche'. "
    "Nutze konkrete Zahlen und schreibe die Alternative direkt in die Empfehlung mit 'Alternative:'. "
    "WICHTIG: Wenn Body Battery < 50 ODER Sleep < 60, dann IMMER nur Recovery-Training mit Intensitaet 1-4. "
    "Die Begründung MUSS explizit auf die aktuellen Gesundheitsdaten Bezug nehmen (Sleep Score, Body Battery, Stress, VO2Max, RHR, letzte Aktivität). "
    "WICHTIG: Verwende in der Begründung IMMER den exakten Zielnamen aus dem Nutzerprofil. Keine Synonyme oder Abwandlungen. "
    "Keine allgemeinen Floskeln."
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
        
        activity_type = str(activity.get("activity_type", "")).lower()
        distance_m = activity.get("distance")
        distance_km = distance_m / 1000.0 if distance_m else None
        
        # Build a clear representation depending on activity type
        entry = {
            "date": activity.get("date"),
            "activity_type": activity.get("activity_type"),
            "duration_seconds": activity.get("duration"),
            "calories": activity.get("calories"),
        }
        
        # Add distance info for cardio activities
        if distance_km is not None:
            entry["distance_km"] = round(distance_km, 2)
        
        # Add primary metric with clear label
        primary_metric = activity.get("primary_metric")
        if "run" in activity_type or "cycling" in activity_type or "swim" in activity_type:
            # For cardio: primary_metric is training effect score
            entry["training_effect_score"] = primary_metric
        elif "strength" in activity_type:
            # For strength: primary_metric is exercise info
            entry["exercises"] = primary_metric
        else:
            entry["primary_metric"] = primary_metric
        
        compact.append(entry)
    return compact


def _calculate_goal_intensity_baseline(goal: str) -> int:
    """Calculate base intensity for the selected goal, before health state adjustments."""
    goal_lower = goal.lower()
    if "kraft fokus" in goal_lower:
        return 9  # Strength focus should be high intensity
    if "kraft" in goal_lower and "ausdauer" in goal_lower:
        return 7  # Balanced goal is moderate-high intensity
    if "ausdauer" in goal_lower or "marathon" in goal_lower:
        return 7  # Endurance focus is moderate-high intensity
    return 6  # Default fallback


def build_coach_prompt(profile: CoachProfile, daily_stats: dict[str, Any], activities: list[dict[str, Any]]) -> str:
    goal_intensity_baseline = _calculate_goal_intensity_baseline(profile.goal)
    
    # Determine recovery status for explicit warning in prompt
    latest_day = _latest_stat_day(daily_stats)
    sleep_score = _as_number(latest_day.get("sleep_score"))
    body_battery = _as_number(latest_day.get("body_battery"))
    recovery_low = (sleep_score is not None and sleep_score < 60) or (body_battery is not None and body_battery < 50)
    
    user_payload = {
        "nutzerprofil": {
            "mobilitaet": profile.mobility,
            "praeferenz": profile.preference,
            "ziel": profile.goal,
            "ziel_intensitaets_basis": goal_intensity_baseline,
        },
        "gesundheitsstatus": {
            "sleep_score": sleep_score,
            "body_battery": body_battery,
            "recovery_kritisch": recovery_low,
            "warnung": "RECOVERY MODE" if recovery_low else "normal",
        },
        "historie_7_tage": _compact_daily_stats(daily_stats),
        "letzte_aktivitaeten": _compact_activities(activities),
        "ausgabeformat": {
            "titel": "...",
            "empfehlung": "...",
            "intensitaet": goal_intensity_baseline if not recovery_low else 3,
            "begruendung": "...",
        },
        "regeln": [
            "Beschreibe nur die naechste konkrete Einheit oder maximal die naechsten 1-2 Einheiten.",
            "Erwaehne keine Wochenfrequenz, keinen Plan und keine Routine.",
            "KRITISCH: Wenn Schlaf < 60 oder Body Battery < 50, dann schlage RECOVERY-TRAINING vor, nie High-Intensity. Empfehle Ruhe, lockeres Gehen, easy Yoga oder maximale Intensitaet 3-4.",
            f"NOTFALL: recovery_kritisch={recovery_low} - Falls TRUE, IMMER Intensitaet 1-4, egal welches Trainingsziel!",
            "AKTIVITAETEN: distance_km = absolute Distanz der letzten Aktivitaet, training_effect_score = aerober/anaerober Reiz-Score (1-5). Verwechsle diese NICHT!",
            "Wenn die Daten gut sind und das Ziel Marathon ist, bevorzuge einen konkreten Longrun, Tempolauf oder Lauftechnik-Session statt einer allgemeinen Regel.",
            "Keine Lauf-Intervalle fuer Rollstuhlfahrer; stattdessen Handbike oder Oberkoerper-Kraft-Ausdauer.",
            "Outdoor bevorzugen, wenn die Erholung nicht kritisch ist.",
            "Fuer 'Ausdauer Fokus': Intensitaet sollte 6-8 sein, priorisiere konkrete Lauf- oder Radreiz.",
            "Fuer 'Kraft Fokus': Intensitaet sollte 8-10 sein (aber NICHT bei niedriger Recovery!), priorisiere konkrete Gym- oder Kraft-Session.",
            "Fuer 'Kraft und Ausdauer maximieren': Intensitaet sollte 6-8 sein, kombiniere klaren Reiz mit realistischer Alternative.",
            "KRITISCH: Verwende den exakten Zielnamen \"" + profile.goal + "\" in der Begruendung, nie Synonyme oder Abwandlungen.",
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


class GroqCoachClient:
    def __init__(self, api_key: str, model_name: str = DEFAULT_GROQ_MODEL_NAME) -> None:
        self._client = Groq(api_key=api_key)
        self._model_name = model_name

    def generate_content(self, prompt: str) -> Any:
        response = self._client.chat.completions.create(
            model=self._model_name,
            messages=[
                {
                    "role": "system",
                    "content": COACH_SYSTEM_PROMPT,
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=450,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content if response.choices else ""
        return type("GroqTextResponse", (), {"text": content or ""})()


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


def _is_retryable_provider_error(exc: Exception) -> bool:
    message = str(exc).upper()
    if "EXCEEDED YOUR CURRENT QUOTA" in message or "INSUFFICIENT_QUOTA" in message:
        return False

    retry_markers = [
        "503",
        "UNAVAILABLE",
        "429",
        "RESOURCE_EXHAUSTED",
        "DEADLINE_EXCEEDED",
        "INTERNAL",
        "EXPECTING VALUE",
        "RATE LIMIT",
    ]
    return any(marker in message for marker in retry_markers)


def _to_intensity(value: Any, default: int = 5) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(1, min(10, number))


def _concrete_next_training(profile: CoachProfile, daily_stats: dict[str, Any], activities: list[dict[str, Any]]) -> dict[str, Any]:
    latest_day = _latest_stat_day(daily_stats)
    sleep_score = _as_number(latest_day.get("sleep_score"))
    body_battery = _as_number(latest_day.get("body_battery"))
    stress = _as_number(latest_day.get("stress"))

    latest_activity_type = str((activities[0] if activities else {}).get("activity_type", "")).lower()
    preference = profile.preference.lower()
    goal = profile.goal.lower()
    recovery_low = (
        (sleep_score is not None and sleep_score < 60)
        or (body_battery is not None and body_battery < 50)  # Changed from 40 to 50 for better recovery awareness
    )

    if "marathon" in goal:
        if recovery_low:
            return {
                "titel": "Marathon-Recovery",
                "empfehlung": "Heute: 30-40 Min lockerer Dauerlauf in Zone 2 oder 45 Min zuegiges Gehen, danach 10 Min Mobility. Alternative: 45 Min Bein- und Coretraining im Gym mit moderater Last.",
                "intensitaet": 3,
                "begruendung": f"Fuer dein Marathon-Ziel ist Erholung heute sinnvoller als ein harter Reiz, weil Sleep {sleep_score if sleep_score is not None else 'n/a'} und Body Battery {body_battery if body_battery is not None else 'n/a'} Erholung priorisieren.",
            }

        if sleep_score is not None and sleep_score >= 75 and body_battery is not None and body_battery >= 75 and (stress is None or stress <= 20):
            return {
                "titel": "Marathon-Longrun",
                "empfehlung": "Heute: 14-16 km lockerer Longrun in Zone 2. Halte das Tempo so, dass du noch sprechen kannst, und laufe die letzten 10 Minuten bewusst sauber und ruhig. Alternative: 50 Min Beintraining im Gym plus 15 Min lockeres Auslaufen.",
                "intensitaet": 6,
                "begruendung": f"Deine Daten sind aktuell stark (Sleep {sleep_score:.0f}, Body Battery {body_battery:.0f}, Stress {stress if stress is not None else 'n/a'}). Fuer Marathon-Aufbau ist ein langer aerober Reiz jetzt sehr passend.",
            }

        return {
            "titel": "Marathon-Aufbau",
            "empfehlung": "Heute: 10 Min einlaufen, dann 6-8 km zuegiger Dauerlauf im stabilen Tempo, anschliessend 4x20 Sekunden lockere Steigerungen und 10 Min auslaufen. Alternative: 45 Min lockeres Radfahren plus 10 Min Core.",
            "intensitaet": 5,
            "begruendung": f"Deine Erholung ist gut genug fuer einen klaren Laufreiz, aber noch nicht maximal. Mit Sleep {sleep_score if sleep_score is not None else 'n/a'} und Body Battery {body_battery if body_battery is not None else 'n/a'} passt ein kontrollierter Aufbau besser als ein Wochenplan.",
        }

    if "ausdauer" in goal:
        if recovery_low:
            return {
                "titel": "Ausdauer-Erholung",
                "empfehlung": "Heute: 30-40 Min lockerer Dauerlauf in Zone 2 oder 45 Min lockeres Radfahren. Alternative: 25 Min zuegiges Gehen plus 10 Min Mobility.",
                "intensitaet": 3,
                "begruendung": f"Das Ausdauerziel bleibt wichtig, aber deine Erholung spricht fuer einen lockeren Reiz (Sleep {sleep_score if sleep_score is not None else 'n/a'}, Body Battery {body_battery if body_battery is not None else 'n/a'}).",
            }
        return {
            "titel": "Ausdauer-Session",
            "empfehlung": "Heute: 12 Min einlaufen, dann 20-25 Min Tempodauerlauf knapp unter Wettkampftempo, danach 10 Min auslaufen. Alternative: 60 Min ruhiger Dauerlauf in Zone 2.",
            "intensitaet": 6,
            "begruendung": f"Deine Werte sind stabil genug fuer einen klaren Ausdauerreiz. Das passt direkt zum Ziel 'Ausdauer Fokus' und nutzt die gute Erholung (Stress {stress if stress is not None else 'n/a'}).",
        }

    if "kraft" in goal and "ausdauer" not in goal:
        if recovery_low:
            return {
                "titel": "Kraft-Erholung",
                "empfehlung": "Heute: 40 Min Technik-Krafttraining mit moderaten Gewichten, 3 Saetze pro Uebung, keine Maximalversuche. Alternative: 30 Min lockeres Radfahren plus Core.",
                "intensitaet": 4,
                "begruendung": f"Fuer das Kraftziel ist ein sauberer, nicht maximaler Reiz sinnvoll, solange Sleep {sleep_score if sleep_score is not None else 'n/a'} und Body Battery {body_battery if body_battery is not None else 'n/a'} nicht noch besser sind.",
            }
        return {
            "titel": "Kraft-Session",
            "empfehlung": "Heute: 50 Min Gym mit Kniebeuge, Druecken, Ziehen und Core. 3-4 Uebungen, 3 Saetze je 8-10 Wiederholungen, kontrollierte Technik. Alternative: 35 Min lockerer Lauf plus 10 Min Mobility.",
            "intensitaet": 9,
            "begruendung": f"Das Kraftziel spricht fuer eine klare Gym-Session. Deine aktuelle Erholung ist gut genug fuer einen strukturierten Reiz (VO2Max {latest_day.get('vo2_max', 'n/a')}, RHR {latest_day.get('resting_heart_rate', 'n/a')}).",
        }

    if "kraft und ausdauer" in goal:
        if recovery_low:
            return {
                "titel": "Balance-Erholung",
                "empfehlung": "Heute: 35 Min lockerer Lauf plus 15 Min Core/Stabi. Alternative: 45 Min Beine/Ganzkoerper im Gym mit moderater Last.",
                "intensitaet": 3,
                "begruendung": f"Dein kombiniertes Ziel bleibt sinnvoll, aber mit Sleep {sleep_score if sleep_score is not None else 'n/a'} und Body Battery {body_battery if body_battery is not None else 'n/a'} ist heute ein lockerer Reiz besser.",
            }
        return {
            "titel": "Balance-Session",
            "empfehlung": "Heute: 10 Min einlaufen, dann 4x5 Min zuegig laufen mit 2 Min locker, danach 10 Min auslaufen. Alternative: 45 Min Gym Ganzkoerper mit 3 Uebungen plus 10 Min lockerer Lauf.",
            "intensitaet": 6,
            "begruendung": f"Fuer Kraft und Ausdauer maximieren ist heute ein kombinierter, konkreter Reiz passend. Du hast gute Voraussetzungen durch Sleep {sleep_score if sleep_score is not None else 'n/a'} und Body Battery {body_battery if body_battery is not None else 'n/a'}.",
        }

    if recovery_low:
        return {
            "titel": "Regenerationseinheit",
            "empfehlung": "Hauptteil: 25-35 Min sehr locker (Spaziergang, lockeres Rad oder easy Jog) + 10 Min Mobility/Huefte/Ruecken. Alternative: 20 Min lockeres Ergometer + 10 Min Dehnen.",
            "intensitaet": 2,
            "begruendung": f"Deine Erholung ist reduziert (Sleep {sleep_score if sleep_score is not None else 'n/a'}, Body Battery {body_battery if body_battery is not None else 'n/a'}). Heute bringt ein lockerer Reiz mehr als hohe Last.",
        }

    if "strength" in latest_activity_type:
        return {
            "titel": "Ausdauer mit Technikfokus",
            "empfehlung": "Heute: 40-50 Min lockerer Dauerlauf oder lockere Radrunde in Zone 2 mit gleichmaessigem Tempo. Alternative: 30 Min zuegiges Gehen plus 15 Min Core/Stabi.",
            "intensitaet": 5,
            "begruendung": f"Nach der letzten Krafteinheit ist ein aerober Reiz sinnvoll, um Erholung zu foerdern und trotzdem Trainingsreiz zu setzen. Dein Stress liegt bei {stress if stress is not None else 'n/a'}.",
        }

    if "run" in latest_activity_type or "cycling" in latest_activity_type or "drau" in preference:
        return {
            "titel": "Strukturierte Ausdauereinheit",
            "empfehlung": "Heute: 10 Min einlaufen, dann 4x4 Min zuegig (RPE 7/10) mit 3 Min locker dazwischen, danach 10 Min auslaufen. Alternative: 45 Min lockerer Dauerlauf in Zone 2.",
            "intensitaet": 7,
            "begruendung": f"Deine aktuellen Werte erlauben einen klaren Belastungsreiz; der Wechsel aus Tempo und Erholung setzt einen konkreten Stimulus (Stress {stress if stress is not None else 'n/a'}).",
        }

    return {
        "titel": "Ganzkoerper-Session",
        "empfehlung": "Heute: 35-45 Min Ganzkoerper (Kniebeuge/Druecken/Ziehen/Core), 3 Saetze je Uebung mit sauberer Technik und moderater Last. Alternative: 40 Min lockere Ausdauereinheit plus 10 Min Mobility.",
        "intensitaet": 6,
        "begruendung": "Die Daten zeigen keine harte Restriktion. Eine strukturierte, konkrete Einheit ist sinnvoller als eine allgemeine Wochenvorgabe.",
    }


def _needs_enrichment(recommendation: dict[str, Any]) -> bool:
    rec_text = str(recommendation.get("empfehlung", "")).strip().lower()
    reason = str(recommendation.get("begruendung", "")).strip().lower()

    if not rec_text or len(rec_text) < 45:
        return True
    generic_markers = [
        "pro woche",
        "2-3",
        "3-4",
        "zweimal",
        "2 mal",
        "3 mal",
        "routine",
        "trainingsplan",
        "wochenplan",
        "woche",
    ]
    if any(marker in rec_text for marker in generic_markers):
        return True
    if "alternative:" not in rec_text:
        return True
    if not any(marker in rec_text for marker in ["heute:", "jetzt:", "morgen:"]):
        return True
    if reason in {"", "n/a", "na", "none"}:
        return True
    if not any(metric in reason for metric in ["sleep", "body battery", "stress", "vo2", "rhr", "ruhepuls", "aktivität", "aktivitaet"]):
        return True
    return False


def _fix_goal_references(text: str, correct_goal: str) -> str:
    """Fix incorrect goal references in coach output to use the exact goal name."""
    if not text or not correct_goal:
        return text
    
    goal_lower = correct_goal.lower()
    text_lower = text.lower()
    
    # Map common wrong references to the correct goal
    if "kraft fokus" in goal_lower:
        # Replace wrong alternatives for "Kraft Fokus"
        wrong_terms = ["kraftausdauer", "kraft und ausdauer", "ausdauerziel", "ausdauer fokus"]
        for term in wrong_terms:
            if term in text_lower:
                # Case-insensitive replacement
                text = re.sub(re.escape(term), correct_goal, text, flags=re.IGNORECASE)
    elif "ausdauer fokus" in goal_lower:
        # Replace wrong alternatives for "Ausdauer Fokus"
        wrong_terms = ["kraft fokus", "kraft und ausdauer", "kraftziel", "kraft-ziel"]
        for term in wrong_terms:
            if term in text_lower:
                text = re.sub(re.escape(term), correct_goal, text, flags=re.IGNORECASE)
    elif "kraft und ausdauer" in goal_lower:
        # Replace wrong alternatives for combined goal
        wrong_terms = ["kraft fokus", "ausdauer fokus", "kraftausdauer"]
        for term in wrong_terms:
            if term in text_lower:
                text = re.sub(re.escape(term), correct_goal, text, flags=re.IGNORECASE)
    
    return text


def _enrich_recommendation(
    recommendation: dict[str, Any],
    profile: CoachProfile,
    daily_stats: dict[str, Any],
    activities: list[dict[str, Any]],
) -> dict[str, Any]:
    base = _concrete_next_training(profile, daily_stats, activities)
    result = dict(recommendation)

    if _needs_enrichment(result):
        return base

    # Check recovery status - if recovery is low, always use base recommendation
    latest_day = _latest_stat_day(daily_stats)
    sleep_score = _as_number(latest_day.get("sleep_score"))
    body_battery = _as_number(latest_day.get("body_battery"))
    
    recovery_low = (
        (sleep_score is not None and sleep_score < 60)
        or (body_battery is not None and body_battery < 50)  # Lowered threshold from 40 to 50
    )
    
    # If recovery is low, always use the safe fallback recommendation
    if recovery_low:
        return base

    result.setdefault("titel", base["titel"])
    
    # Adjust intensity based on goal if needed
    current_intensity = _to_intensity(result.get("intensitaet"), 5)
    goal_baseline = _calculate_goal_intensity_baseline(profile.goal)
    if current_intensity < goal_baseline - 1:
        result["intensitaet"] = goal_baseline
    else:
        result["intensitaet"] = current_intensity
    
    if not str(result.get("begruendung", "")).strip() or str(result.get("begruendung", "")).strip().lower() in {"n/a", "na"}:
        result["begruendung"] = base["begruendung"]
    else:
        # Fix goal references in begruendung
        result["begruendung"] = _fix_goal_references(result["begruendung"], profile.goal)
    
    if "alternative:" not in str(result.get("empfehlung", "")).lower():
        result["empfehlung"] = f"{str(result.get('empfehlung', '')).strip()} Alternative: {base['empfehlung'].split('Alternative:', 1)[-1].strip()}"
    
    return result


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
    return _concrete_next_training(profile, daily_stats, activities)


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
    last_error: Exception | None = None

    for attempt in range(1, 4):
        try:
            response = client.generate_content(prompt)
            response_text = getattr(response, "text", "") or ""
            recommendation = _extract_json_response(response_text)
            recommendation = _enrich_recommendation(recommendation, profile, daily_stats, activities)
            recommendation["source"] = "model"
            recommendation["model_attempt"] = attempt
            _save_cached_recommendation(recommendation)
            return recommendation
        except Exception as exc:
            last_error = exc
            if attempt < 3 and _is_retryable_provider_error(exc):
                time.sleep(float(attempt))
                continue
            break

    recommendation = _local_recommendation(profile, daily_stats, activities)
    recommendation["source"] = "local"
    if last_error is not None:
        recommendation["fallback_reason"] = f"Provider-Fehler nach 3 Versuchen: {str(last_error)[:220]}"
    _save_cached_recommendation(recommendation)
    return recommendation


def get_coach_recommendation(
    profile: CoachProfile,
    daily_stats: dict[str, Any] | None = None,
    activities: list[dict[str, Any]] | None = None,
    refresh: bool = False,
    model_name: str = DEFAULT_GROQ_MODEL_NAME,
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
        recommendation["fallback_reason"] = "GROQ_CLOUD_KEY fehlt oder ist leer."
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
    parser.add_argument("--run-model", action="store_true", help="Direkt Groq ansprechen")
    parser.add_argument("--model", default=DEFAULT_GROQ_MODEL_NAME, help="Groq-Modellname")
    parser.add_argument("--refresh", action="store_true", help="Cache ignorieren und neue Empfehlung anfordern")
    return parser.parse_args()


def _build_client(model_name: str) -> Any:
    groq_api_key = os.getenv("GROQ_CLOUD_KEY", "").strip()
    if groq_api_key:
        selected_model = model_name or DEFAULT_GROQ_MODEL_NAME
        return GroqCoachClient(api_key=groq_api_key, model_name=selected_model)

    return None


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