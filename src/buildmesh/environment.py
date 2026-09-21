"""Environmental context normalization, freshness, and transparent rule scoring."""
from __future__ import annotations

from datetime import UTC, datetime
from datetime import date
from math import acos, cos, degrees, radians, sin
from typing import Any

EPISTEMIC = {"VERIFIED", "INFERRED", "ASSUMED", "UNKNOWN", "CONFLICTING", "STALE", "NEEDS_REVIEW"}
KINDS = {"weather_observation", "weather_forecast", "historical_climate", "solar_context", "wind_context", "traffic_context", "local_event", "terrain_context", "soil_context"}
FRESHNESS_HOURS = {"weather_observation": 3, "weather_forecast": 6, "traffic_context": 2, "local_event": 24, "solar_context": 24, "wind_context": 3}
ACTIVITY_HEURISTICS = {
    "excavation": {"rain_probability": 0.55, "precipitation_mm": 5.0, "weight": 0.55, "severity": "high"},
    "foundation_work": {"rain_probability": 0.60, "precipitation_mm": 5.0, "weight": 0.5, "severity": "high"},
    "concrete_placement": {"rain_probability": 0.50, "precipitation_mm": 3.0, "weight": 0.6, "severity": "high"},
    "roofing": {"rain_probability": 0.45, "precipitation_mm": 2.0, "weight": 0.6, "severity": "high"},
    "exterior_finishing": {"rain_probability": 0.50, "precipitation_mm": 3.0, "weight": 0.5, "severity": "medium"},
    "painting": {"rain_probability": 0.45, "precipitation_mm": 2.0, "weight": 0.55, "severity": "medium"},
    "road_work": {"rain_probability": 0.40, "precipitation_mm": 2.0, "weight": 0.65, "severity": "high"},
}


def parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("environmental timestamp must be ISO-8601") from exc


def normalize(kind: str, source: str, source_type: str, retrieved_at: str, observed_at: str, latitude: float, longitude: float, values: dict[str, Any], units: dict[str, str], epistemic_state: str = "VERIFIED", fixture: bool = False, confidence: float | None = None, valid_until: str | None = None) -> dict[str, Any]:
    if kind not in KINDS or not isinstance(source, str) or not source.strip() or source_type not in {"live", "fixture", "manual", "calculated", "historical"}:
        raise ValueError("environmental kind, source, or source_type is invalid")
    if fixture and source_type != "fixture":
        raise ValueError("fixture environmental data must use source_type fixture")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or epistemic_state not in EPISTEMIC:
        raise ValueError("environmental geographic scope or epistemic state is invalid")
    retrieved, observed = parse_time(retrieved_at), parse_time(observed_at)
    if valid_until and parse_time(valid_until) < observed:
        raise ValueError("environmental valid_until cannot precede observed_at")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
        raise ValueError("environmental confidence must be between 0 and 1")
    allowed = {"temperature_c", "precipitation_mm", "rain_probability", "humidity_percent", "wind_speed_kph", "wind_direction_degrees", "cloud_cover_percent", "daylight_hours", "congestion_index", "disruption_level", "soil_type", "elevation_m", "slope_percent", "summary", "window_start", "window_end"}
    if not isinstance(values, dict) or set(values) - allowed or not isinstance(units, dict):
        raise ValueError("environmental values or units are invalid")
    ranges = {"rain_probability": (0, 1), "humidity_percent": (0, 100), "wind_speed_kph": (0, 500), "wind_direction_degrees": (0, 360), "cloud_cover_percent": (0, 100), "daylight_hours": (0, 24), "congestion_index": (0, 1), "disruption_level": (0, 1), "precipitation_mm": (0, 5000), "slope_percent": (0, 1000), "temperature_c": (-100, 100)}
    for key, bounds in ranges.items():
        if key in values and (isinstance(values[key], bool) or not isinstance(values[key], (int, float)) or not bounds[0] <= float(values[key]) <= bounds[1]):
            raise ValueError(f"environmental {key} is out of bounds")
    age_hours = max(0.0, (datetime.now(UTC) - retrieved.astimezone(UTC)).total_seconds() / 3600)
    freshness = FRESHNESS_HOURS.get(kind)
    # Deterministic fixtures use fixed historical timestamps. They remain usable for
    # repeatable evaluation unless explicitly ancient; live/manual sources use the
    # normal short freshness window.
    stale = freshness is not None and age_hours > freshness and (source_type != "fixture" or age_hours > 24 * 365)
    return {"kind": kind, "source": source.strip(), "source_type": source_type, "retrieved_at": retrieved_at, "observed_at": observed_at, "valid_until": valid_until, "geographic_scope": {"latitude": latitude, "longitude": longitude, "crs": "WGS84"}, "values": values, "units": units, "epistemic_state": "STALE" if stale else epistemic_state, "freshness": {"expected_hours": freshness, "actual_age_hours": round(age_hours, 3), "status": "STALE" if stale else "FRESH" if freshness is not None else "NOT_APPLICABLE"}, "fixture": fixture, "confidence": confidence}


def score(activity: str, contexts: list[dict[str, Any]]) -> dict[str, Any]:
    if activity not in ACTIVITY_HEURISTICS:
        raise ValueError("unsupported activity for environmental scoring")
    rule, factors, missing = ACTIVITY_HEURISTICS[activity], [], []
    for context in contexts:
        values = context["payload"].get("values", {})
        if context["payload"].get("epistemic_state") == "STALE":
            factors.append({"factor": "stale_context", "evidence_id": context["id"], "weight": 0, "effect": "requires refresh or review"})
            continue
        probability = values.get("rain_probability")
        precipitation = values.get("precipitation_mm")
        if probability is not None:
            factors.append({"factor": "rain_probability", "value": probability, "threshold": rule["rain_probability"], "evidence_id": context["id"], "effect": "risk" if probability >= rule["rain_probability"] else "neutral"})
        if precipitation is not None:
            factors.append({"factor": "precipitation_mm", "value": precipitation, "threshold": rule["precipitation_mm"], "evidence_id": context["id"], "effect": "risk" if precipitation >= rule["precipitation_mm"] else "neutral"})
    if not factors:
        missing.append("weather context")
    risks = sum(item.get("effect") == "risk" for item in factors)
    usable = [item for item in factors if item["factor"] != "stale_context"]
    confidence = round(min(1.0, len(usable) / 2), 2)
    suitability = round(max(0, 100 - risks * rule["weight"] * 100), 1) if usable else None
    return {"activity": activity, "suitability_score": suitability, "confidence": confidence, "factors": factors, "missing_factors": missing, "formula": "score=100-(risk_factor_count × configured activity weight × 100); missing factors lower confidence and do not equal zero", "behavior": {"missing": "lowers confidence; never assumed safe", "stale": "excluded from current suitability and requires review", "conflict": "retained as separate CONFLICTING evidence and reviewed by ContextFusionAgent"}, "heuristic": {**rule, "status": "configurable planning heuristic; not engineering standard"}}


def solar(latitude: float, longitude: float, for_date: str) -> dict[str, Any]:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("coordinates are outside WGS84 bounds")
    try:
        target = date.fromisoformat(for_date)
    except ValueError as exc:
        raise ValueError("solar date must be YYYY-MM-DD") from exc
    declination = radians(-23.44 * cos(radians((360 / 365) * (target.timetuple().tm_yday + 10))))
    latitude_radians = radians(latitude)
    cosine = (sin(radians(-0.833)) - sin(latitude_radians) * sin(declination)) / (cos(latitude_radians) * cos(declination))
    daylight = 0.0 if cosine >= 1 else 24.0 if cosine <= -1 else round(2 * degrees(acos(cosine)) / 15, 2)
    return {"daylight_hours": daylight, "summary": "approximate solar geometry; no CAD occlusion or ray tracing"}


def historical_windows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {"label", "data_period", "precipitation_tendency", "temperature_c", "daylight_hours", "data_coverage", "source", "confidence"}:
            raise ValueError("historical candidate schema is invalid")
        rain, coverage = candidate["precipitation_tendency"], candidate["data_coverage"]
        if not isinstance(rain, (int, float)) or not isinstance(coverage, (int, float)) or not 0 <= rain <= 1 or not 0 <= coverage <= 1:
            raise ValueError("historical candidate risk and coverage must be probabilities")
        result.append({**candidate, "suitability_score": round(max(0, 100 - 70 * rain + 10 * coverage), 1), "epistemic_state": "INFERRED", "explanation": "historical tendency, not a forecast"})
    return result
