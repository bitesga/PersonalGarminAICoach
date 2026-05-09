from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def fetch_current_weather(latitude: float, longitude: float, timezone: str = "Europe/Berlin") -> dict[str, Any] | None:
    query = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "current": "temperature_2m,wind_speed_10m,precipitation",
        "timezone": timezone,
        "forecast_days": "1",
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    current = payload.get("current", {}) if isinstance(payload, dict) else {}
    if not isinstance(current, dict):
        return None
    return {
        "source": "open-meteo",
        "time": str(current.get("time", "")),
        "temperature_c": _to_number(current.get("temperature_2m")),
        "wind_speed_kmh": _to_number(current.get("wind_speed_10m")),
        "precipitation_mm": _to_number(current.get("precipitation")),
        "timezone": str(payload.get("timezone", timezone)),
        "latitude": latitude,
        "longitude": longitude,
    }
