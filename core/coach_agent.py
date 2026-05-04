"""Build and run an adaptive coach prompt from Garmin JSON data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from groq import Groq

from core.data_persistence import load_coach_recommendation, save_coach_recommendation


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
PROMPT_ASSETS_PATH = DATA_DIR / "coach_examples.json"
LLM_RAW_LOG_PATH = DATA_DIR / "llm_raw_responses.log"
DEFAULT_GROQ_MODEL_NAME = "llama-3.3-70b-versatile"
CACHE_TTL_HOURS = 6
# If Body Battery drops under this threshold, recommend an explicit full rest day (Rest Day)
RUHETAG_BODY_BATTERY_THRESHOLD = 35

@dataclass(frozen=True)
class CoachProfile:
    """User context that shapes the recommendation."""

    mobility: str
    preference: str
    goal: str


COACH_SYSTEM_PROMPT = (
    "You are a precise fitness coach with ABSOLUTE priority on overload protection. "
    "Reply ONLY as JSON with the keys title, recommendation, alternative, intensity, reasoning. "
    "The recommendation MUST describe exactly the next concrete session or at most the next 1-2 sessions (duration, structure, intensity). "
    "No weekly plans, no routines, no frequency statements like '2 times per week'. "
    "Use concrete numbers and ALWAYS include an alternative as its own key (alternative). "
    "IMPORTANT: If Body Battery < 50 OR Sleep < 60, then ONLY recovery training with intensity 1-4. "
    "The reasoning MUST explicitly reference current health data (Sleep Score, Body Battery, Stress, VO2Max, RHR, last activity). "
    "IMPORTANT: In the reasoning, ALWAYS use the exact goal name from the user profile. No synonyms or paraphrases. "
    "No generic phrases. "
    "IMPORTANT: If Body Battery is around 35 or lower, say that no training should be done today and return title 'Rest Day'."
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


def _load_prompt_assets() -> dict[str, Any]:
    payload = _load_json_file(PROMPT_ASSETS_PATH, {})
    if not isinstance(payload, dict):
        return {"examples": [], "fallbacks": {}}
    payload.setdefault("examples", [])
    payload.setdefault("fallbacks", {})
    return payload


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "n/a"


def _format_metric(value: Any, fmt: str | None = None) -> str:
    if value is None:
        return "n/a"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if fmt:
        return format(num, fmt)
    if num.is_integer():
        return f"{int(num)}"
    return f"{num:.1f}"


def _build_fallback_context(latest_day: dict[str, Any]) -> dict[str, str]:
    return {
        "sleep_score": _format_metric(latest_day.get("sleep_score"), ".0f"),
        "body_battery": _format_metric(latest_day.get("body_battery"), ".0f"),
        "stress": _format_metric(latest_day.get("stress")),
        "vo2_max": _format_metric(latest_day.get("vo2_max")),
        "resting_heart_rate": _format_metric(latest_day.get("resting_heart_rate")),
    }


def _log_llm_raw_response(raw_text: str) -> None:
    try:
        LLM_RAW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().isoformat()
        payload = {
            "timestamp": timestamp,
            "raw_text": raw_text,
        }
        with LLM_RAW_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort logging; ignore any filesystem issues.
        pass


def _select_fallback_template(assets: dict[str, Any], key: str) -> dict[str, Any] | None:
    fallbacks = assets.get("fallbacks", {})
    if not isinstance(fallbacks, dict):
        return None
    entry = fallbacks.get(key)
    if isinstance(entry, list) and entry:
        return random.choice([item for item in entry if isinstance(item, dict)])
    if isinstance(entry, dict):
        return entry
    return None


def _render_fallback(key: str, latest_day: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    template = _select_fallback_template(assets, key) or {}
    context = _SafeFormatDict(_build_fallback_context(latest_day))
    return {
        "title": str(template.get("title", "Recommendation")).format_map(context),
        "recommendation": str(template.get("recommendation", "No recommendation available.")).format_map(context),
        "alternative": str(template.get("alternative", "")).format_map(context),
        "intensity": template.get("intensity", 5),
        "reasoning": str(template.get("reasoning", "")).format_map(context),
    }


def _normalize_recommendation_keys(recommendation: dict[str, Any]) -> dict[str, Any]:
    result = dict(recommendation or {})

    title = result.get("title")
    if not title:
        title = result.get("titel")

    recommendation_text = result.get("recommendation")
    if not recommendation_text:
        recommendation_text = result.get("empfehlung")

    alternative = result.get("alternative")
    if not alternative:
        alt_raw = str(recommendation_text or "")
        if "Alternative:" in alt_raw:
            main_reco, alt_reco = alt_raw.split("Alternative:", 1)
            recommendation_text = main_reco.strip()
            alternative = alt_reco.strip()

    intensity = result.get("intensity")
    if intensity is None:
        intensity = result.get("intensitaet")

    reasoning = result.get("reasoning")
    if not reasoning:
        reasoning = result.get("begruendung")

    result.update(
        {
            "title": title or "Recommendation",
            "recommendation": recommendation_text or "",
            "alternative": alternative or "",
            "intensity": intensity,
            "reasoning": reasoning or "",
        }
    )
    return result


def load_coach_inputs(data_dir: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base_dir = data_dir or DATA_DIR
    daily_stats = _load_json_file(base_dir / "daily_stats.json", {})
    activities_payload = _load_json_file(base_dir / "activities.json", {"activities": []})
    activities = activities_payload.get("activities", []) if isinstance(activities_payload, dict) else []
    return daily_stats, activities


def _load_cached_recommendation(user_id: str | None = None) -> dict[str, Any] | None:
    payload = load_coach_recommendation(user_id=user_id)
    if not payload or not isinstance(payload, dict):
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


def _save_cached_recommendation(recommendation: dict[str, Any], user_id: str | None = None) -> None:
    try:
        save_coach_recommendation(recommendation, user_id=user_id)
    except Exception:
        # best-effort: fall back to writing into global data dir if persistence fails
        fallback = Path(__file__).resolve().parents[1] / "data" / "coach_recommendation.json"
        fallback.parent.mkdir(exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(),
            "recommendation": recommendation,
        }
        fallback.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


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
    if "strength focus" in goal_lower or "kraft fokus" in goal_lower:
        return 9  # Strength focus should be high intensity
    if (
        "build strength and endurance" in goal_lower
        or "strength and endurance" in goal_lower
        or "kraft und ausdauer" in goal_lower
        or ("kraft" in goal_lower and "ausdauer" in goal_lower)
    ):
        return 7  # Balanced goal is moderate-high intensity
    if "endurance focus" in goal_lower or "ausdauer" in goal_lower or "marathon" in goal_lower:
        return 7  # Endurance focus is moderate-high intensity
    return 6  # Default fallback


def build_coach_prompt(profile: CoachProfile, daily_stats: dict[str, Any], activities: list[dict[str, Any]]) -> str:
    assets = _load_prompt_assets()
    goal_intensity_baseline = _calculate_goal_intensity_baseline(profile.goal)
    
    # Determine recovery status for explicit warning in prompt
    latest_day = _latest_stat_day(daily_stats)
    sleep_score = _as_number(latest_day.get("sleep_score"))
    body_battery = _as_number(latest_day.get("body_battery"))
    recovery_low = (sleep_score is not None and sleep_score < 60) or (body_battery is not None and body_battery < 50)
    # Very low body battery triggers an explicit "Ruhetag" (no-training today)
    recovery_ruhetag = body_battery is not None and body_battery < RUHETAG_BODY_BATTERY_THRESHOLD
    
    # Extract training load metrics for intensity adjustment
    training_load_acute = _as_number(latest_day.get("training_load_acute"))
    training_balance_feedback = str(latest_day.get("training_balance_feedback", "")).strip()
    
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
            "recovery_ruhetag": recovery_ruhetag,
            "warnung": "RECOVERY MODE" if recovery_low else "normal",
        },
        "trainingsbelastung": {
            "acute_training_load": training_load_acute,
            "training_balance_feedback": training_balance_feedback,
        },
        "historie_7_tage": _compact_daily_stats(daily_stats),
        "letzte_aktivitaeten": _compact_activities(activities),
        "ausgabeformat": {
            "title": "...",
            "recommendation": "...",
            "alternative": "...",
            "intensity": goal_intensity_baseline if not recovery_low else 3,
            "reasoning": "...",
        },
        "beispiele": assets.get("examples", []),
        "regeln": [
            "Describe only the next concrete session or at most the next 1-2 sessions.",
            "No weekly frequency, no plan, no routine.",
            "CRITICAL: If Sleep < 60 or Body Battery < 50, suggest ONLY recovery training, never high intensity. Recommend rest, easy walk, easy yoga; max intensity 3-4.",
            f"EMERGENCY: recovery_kritisch={recovery_low} - If TRUE, ALWAYS intensity 1-4 regardless of the training goal.",
            f"EMERGENCY_RUHETAG: recovery_ruhetag={recovery_ruhetag} - If TRUE, return title 'Rest Day' and explicitly say no training today.",
            "ACTIVITIES: distance_km = absolute distance of the last activity; training_effect_score = aerobic/anaerobic stimulus score (1-5). Do NOT mix them up.",
            f"TRAINING LOAD: acute_training_load={training_load_acute} - If high (e.g., > 200), reduce recommended intensity by 2-3 points.",
            f"TRAINING BALANCE: training_balance_feedback='{training_balance_feedback}' - If 'AEROBIC_HIGH_SHORTAGE': recommend high intensity aerobic if aligned with goal. If 'AEROBIC_LOW_SHORTAGE': recommend low intensity aerobic. If 'ANAEROBIC': recommend strength/anaerobic stimulus.",
            "If data is strong and goal is Marathon, prefer a concrete long run, tempo run, or technique session over a general rule.",
            "No running intervals for wheelchair users; suggest handbike or upper-body strength-endurance instead.",
            "Prefer outdoor sessions when recovery is not critical.",
            "For 'Endurance Focus': intensity should be 6-8, prioritize a concrete run or bike stimulus.",
            "For 'Strength Focus': intensity should be 8-10 (but NOT with low recovery), prioritize a concrete gym or strength session.",
            "For 'Build Strength and Endurance': intensity should be 6-8, combine a clear stimulus with a realistic alternative.",
            "CRITICAL: In the reasoning, use the exact goal name \"" + profile.goal + "\". No synonyms.",
            "CRITICAL: Always include an alternative as its own key (alternative).",
        ],
    }
    return (
        f"{COACH_SYSTEM_PROMPT}\n"
        f"{json.dumps(user_payload, ensure_ascii=False, separators=(',', ':'), default=str)}\n"
        "Return JSON only, no Markdown."
    )


def format_coach_message(recommendation: dict[str, Any]) -> str:
    normalized = _normalize_recommendation_keys(recommendation)
    title = str(normalized.get("title", "Recommendation"))
    recommendation_text = str(normalized.get("recommendation", "No recommendation available."))
    alternative = str(normalized.get("alternative", ""))
    intensity = normalized.get("intensity", "n/a")
    reasoning = str(normalized.get("reasoning", ""))
    source = str(recommendation.get("source", "model"))

    lines = [
        f"**{title}**",
        f"Intensity: {intensity}/10",
        f"Recommendation: {recommendation_text}",
        f"Alternative: {alternative or '-'}",
        f"Reasoning: {reasoning}",
        f"Source: {source}",
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
    assets = _load_prompt_assets()
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

    # Explicit Ruhetag: if Body Battery is very low, recommend no training today
    ruhetag = body_battery is not None and body_battery < RUHETAG_BODY_BATTERY_THRESHOLD
    if ruhetag:
        return _render_fallback("ruhetag", latest_day, assets)

    if "marathon" in goal:
        if recovery_low:
            return _render_fallback("marathon_recovery", latest_day, assets)

        if sleep_score is not None and sleep_score >= 75 and body_battery is not None and body_battery >= 75 and (stress is None or stress <= 20):
            return _render_fallback("marathon_longrun", latest_day, assets)

        return _render_fallback("marathon_build", latest_day, assets)

    if "endurance" in goal or "ausdauer" in goal:
        if recovery_low:
            return _render_fallback("ausdauer_recovery", latest_day, assets)
        return _render_fallback("ausdauer_session", latest_day, assets)

    if ("strength" in goal or "kraft" in goal) and "ausdauer" not in goal and "endurance" not in goal:
        if recovery_low:
            return _render_fallback("kraft_recovery", latest_day, assets)
        return _render_fallback("kraft_session", latest_day, assets)

    if "strength and endurance" in goal or "build strength and endurance" in goal or "kraft und ausdauer" in goal:
        if recovery_low:
            return _render_fallback("balance_recovery", latest_day, assets)
        return _render_fallback("balance_session", latest_day, assets)

    if recovery_low:
        return _render_fallback("general_recovery", latest_day, assets)

    if "strength" in latest_activity_type:
        return _render_fallback("post_strength_endurance", latest_day, assets)

    if "run" in latest_activity_type or "cycling" in latest_activity_type or "drau" in preference:
        return _render_fallback("structured_endurance", latest_day, assets)

    return _render_fallback("general_strength", latest_day, assets)


def _needs_enrichment(recommendation: dict[str, Any]) -> bool:
    normalized = _normalize_recommendation_keys(recommendation)
    rec_text = str(normalized.get("recommendation", "")).strip().lower()
    alternative = str(normalized.get("alternative", "")).strip().lower()
    reason = str(normalized.get("reasoning", "")).strip().lower()

    if not rec_text or len(rec_text) < 45:
        return True
    generic_markers = [
        "per week",
        "2-3",
        "3-4",
        "twice",
        "3 times",
        "routine",
        "plan",
        "weekly",
        "week",
        "pro woche",
        "zweimal",
        "2 mal",
        "3 mal",
        "trainingsplan",
        "wochenplan",
        "woche",
    ]
    if any(marker in rec_text for marker in generic_markers):
        return True
    if not alternative and "alternative:" not in rec_text:
        return True
    if not any(marker in rec_text for marker in ["today", "now", "tomorrow", "heute", "jetzt", "morgen"]):
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
    if "strength focus" in goal_lower or "kraft fokus" in goal_lower:
        # Replace wrong alternatives for strength focus
        wrong_terms = [
            "strength and endurance",
            "build strength and endurance",
            "endurance focus",
            "kraftausdauer",
            "kraft und ausdauer",
            "ausdauerziel",
            "ausdauer fokus",
        ]
        for term in wrong_terms:
            if term in text_lower:
                # Case-insensitive replacement
                text = re.sub(re.escape(term), correct_goal, text, flags=re.IGNORECASE)
    elif "endurance focus" in goal_lower or "ausdauer fokus" in goal_lower:
        # Replace wrong alternatives for endurance focus
        wrong_terms = [
            "strength focus",
            "strength and endurance",
            "build strength and endurance",
            "kraft fokus",
            "kraft und ausdauer",
            "kraftziel",
            "kraft-ziel",
        ]
        for term in wrong_terms:
            if term in text_lower:
                text = re.sub(re.escape(term), correct_goal, text, flags=re.IGNORECASE)
    elif "strength and endurance" in goal_lower or "build strength and endurance" in goal_lower or "kraft und ausdauer" in goal_lower:
        # Replace wrong alternatives for combined goal
        wrong_terms = [
            "strength focus",
            "endurance focus",
            "kraft fokus",
            "ausdauer fokus",
            "kraftausdauer",
        ]
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
    result = _normalize_recommendation_keys(recommendation)

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
    # If Body Battery is critically low, prefer explicit Ruhetag fallback
    ruhetag = body_battery is not None and body_battery < RUHETAG_BODY_BATTERY_THRESHOLD
    if ruhetag:
        return base
    
    # If recovery is low, always use the safe fallback recommendation
    if recovery_low:
        return base

    result.setdefault("title", base["title"])
    
    # Adjust intensity based on goal if needed
    current_intensity = _to_intensity(result.get("intensity"), 5)
    goal_baseline = _calculate_goal_intensity_baseline(profile.goal)
    if current_intensity < goal_baseline - 1:
        result["intensity"] = goal_baseline
    else:
        result["intensity"] = current_intensity
    
    if not str(result.get("reasoning", "")).strip() or str(result.get("reasoning", "")).strip().lower() in {"n/a", "na"}:
        result["reasoning"] = base["reasoning"]
    else:
        # Fix goal references in begruendung
        result["reasoning"] = _fix_goal_references(result["reasoning"], profile.goal)
    
    if not str(result.get("alternative", "")).strip():
        result["alternative"] = base.get("alternative", "")
    if not str(result.get("alternative", "")).strip():
        fallback_alt = str(base.get("recommendation", "")).split("Alternative:", 1)
        if len(fallback_alt) > 1:
            result["alternative"] = fallback_alt[1].strip()
    
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
    user_id: str | None = None,
) -> dict[str, Any]:
    if daily_stats is None or activities is None:
        loaded_stats, loaded_activities = load_coach_inputs()
        daily_stats = daily_stats or loaded_stats
        activities = activities or loaded_activities

    if not refresh:
        cached_recommendation = _load_cached_recommendation(user_id=user_id)
        if cached_recommendation is not None:
            return cached_recommendation

    prompt = build_coach_prompt(profile, daily_stats, activities)
    last_error: Exception | None = None

    for attempt in range(1, 4):
        try:
            response = client.generate_content(prompt)
            response_text = getattr(response, "text", "") or ""
            _log_llm_raw_response(response_text)
            recommendation = _extract_json_response(response_text)
            recommendation = _normalize_recommendation_keys(recommendation)
            recommendation = _enrich_recommendation(recommendation, profile, daily_stats, activities)
            recommendation["source"] = "model"
            recommendation["model_attempt"] = attempt
            _save_cached_recommendation(recommendation, user_id=user_id)
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
    _save_cached_recommendation(recommendation, user_id=user_id)
    return recommendation


def get_coach_recommendation(
    profile: CoachProfile,
    daily_stats: dict[str, Any] | None = None,
    activities: list[dict[str, Any]] | None = None,
    refresh: bool = False,
    model_name: str = DEFAULT_GROQ_MODEL_NAME,
    user_id: str | None = None,
) -> dict[str, Any]:
    if daily_stats is None or activities is None:
        loaded_stats, loaded_activities = load_coach_inputs()
        daily_stats = daily_stats or loaded_stats
        activities = activities or loaded_activities

    if not refresh:
        cached_recommendation = _load_cached_recommendation(user_id=user_id)
        if cached_recommendation is not None:
            return cached_recommendation

    client = _build_client(model_name)
    if client is None:
        recommendation = _local_recommendation(profile, daily_stats, activities)
        recommendation["source"] = "local"
        recommendation["fallback_reason"] = "GROQ_CLOUD_KEY fehlt oder ist leer."
        _save_cached_recommendation(recommendation)
        return recommendation

    recommendation = generate_coach_recommendation(
        profile, client, daily_stats, activities, refresh=refresh, user_id=user_id
    )
    _save_cached_recommendation(recommendation, user_id=user_id)
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