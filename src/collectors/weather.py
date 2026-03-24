"""E-class signal collector: Extreme weather + natural disasters.

Data sources (all free, no API key):
  - Open-Meteo: 72h weather forecast per Region coordinate
    Batch API: pass all coordinates in one request to reduce HTTP round-trips.
  - USGS Earthquake: M>=4.0 events within 200km of Region
  - GDACS: Active orange/red disaster alerts

Scoring (0-15):
  Temperature >= 45°C       → +8
  Precipitation >= 20mm/h   → +10
  Wind speed >= 100km/h     → +7
  GDACS orange alert        → +5
  GDACS red alert           → +10
  Earthquake M>=5.0 <100km  → +15
  Earthquake M>=4.0 <200km  → +5
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from shared.types import SignalClass, SignalRecord
from shared.region_config import ALL_REGIONS, RegionConfig
from shared.db import put_signal
from shared.http_client import get_json

logger = logging.getLogger(__name__)

MAX_SCORE = 15


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Open-Meteo batch ──

def _fetch_all_weather(regions: list[RegionConfig]) -> list[dict[str, Any]]:
    """Batch-fetch 72h forecasts for all region coordinates in one HTTP call.

    Open-Meteo supports comma-separated latitude/longitude lists and returns a
    list of forecast objects (one per coordinate pair).
    """
    lats = ",".join(str(r.lat) for r in regions)
    lons = ",".join(str(r.lon) for r in regions)
    data = get_json(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lats,
            "longitude": lons,
            "hourly": "temperature_2m,precipitation,wind_speed_10m",
            "forecast_days": 3,
            "timezone": "UTC",
        },
    )
    if isinstance(data, list):
        return data
    # Single-coordinate response is a plain dict
    return [data]


def _score_weather(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Score weather data for a Region. Returns (score, raw_details)."""
    hourly = data.get("hourly", {})
    temps = hourly.get("temperature_2m", [])
    precips = hourly.get("precipitation", [])
    winds = hourly.get("wind_speed_10m", [])

    max_temp = max(temps) if temps else 0
    max_precip = max(precips) if precips else 0
    max_wind = max(winds) if winds else 0

    score = 0
    alerts = []

    # Extreme heat (data center cooling risk)
    if max_temp >= 45:
        score += 8
        alerts.append(f"extreme_heat:{max_temp:.1f}C")
    elif max_temp >= 40:
        score += 3
        alerts.append(f"heat:{max_temp:.1f}C")

    # Heavy rain (flood risk)
    if max_precip >= 20:
        score += 10
        alerts.append(f"heavy_rain:{max_precip:.1f}mm/h")
    elif max_precip >= 10:
        score += 4
        alerts.append(f"rain:{max_precip:.1f}mm/h")

    # Strong wind
    if max_wind >= 100:
        score += 7
        alerts.append(f"strong_wind:{max_wind:.1f}km/h")
    elif max_wind >= 60:
        score += 3
        alerts.append(f"wind:{max_wind:.1f}km/h")

    return min(score, MAX_SCORE), {
        "max_temp": max_temp,
        "max_precip": max_precip,
        "max_wind": max_wind,
        "alerts": alerts,
    }


# ── USGS Earthquake ──

def _fetch_earthquakes() -> list[dict[str, Any]]:
    """Fetch M>=4.0 earthquakes from the last 24h."""
    data = get_json(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
    )
    return data.get("features", [])


def _score_earthquake(
    region: RegionConfig, quakes: list[dict[str, Any]]
) -> tuple[int, dict[str, Any]]:
    """Score earthquake risk for a Region based on proximity."""
    score = 0
    nearest = None

    for q in quakes:
        coords = q["geometry"]["coordinates"]  # [lon, lat, depth]
        qlon, qlat = coords[0], coords[1]
        mag = q["properties"]["mag"]
        dist = _haversine_km(region.lat, region.lon, qlat, qlon)

        if dist <= 100 and mag >= 5.0:
            score = max(score, 15)
            nearest = {"mag": mag, "dist_km": round(dist), "place": q["properties"]["place"]}
        elif dist <= 200 and mag >= 4.0:
            score = max(score, 5)
            if nearest is None:
                nearest = {"mag": mag, "dist_km": round(dist), "place": q["properties"]["place"]}

    return min(score, MAX_SCORE), {"nearest_quake": nearest}


# ── GDACS ──

def _fetch_gdacs() -> list[dict[str, Any]]:
    """Fetch active GDACS alerts (orange + red)."""
    try:
        data = get_json(
            "https://www.gdacs.org/gdacsapi/api/events/geteventlist/MAP",
            params={"alertlevel": "Orange,Red", "limit": 50},
        )
        return data if isinstance(data, list) else data.get("features", [])
    except Exception as exc:
        logger.warning("GDACS fetch failed: %s", exc)
        return []


def _score_gdacs(
    region: RegionConfig, alerts: list[dict[str, Any]]
) -> tuple[int, dict[str, Any]]:
    """Score GDACS alerts by proximity to Region."""
    score = 0
    relevant = []

    for alert in alerts:
        try:
            geo = alert.get("geometry", {}).get("coordinates", [])
            if not geo:
                continue
            alon, alat = float(geo[0]), float(geo[1])
            dist = _haversine_km(region.lat, region.lon, alat, alon)

            if dist > 500:
                continue

            level = (
                alert.get("properties", {}).get("alertlevel", "").lower()
            )
            if level == "red":
                score = max(score, 10)
            elif level == "orange":
                score = max(score, 5)

            relevant.append({
                "type": alert.get("properties", {}).get("eventtype"),
                "level": level,
                "dist_km": round(dist),
            })
        except (ValueError, KeyError, TypeError):
            continue

    return min(score, MAX_SCORE), {"gdacs_alerts": relevant[:3]}


# ── Per-region computation ──

def _process_region(
    region: RegionConfig,
    weather_data: dict[str, Any],
    quakes: list[dict[str, Any]],
    gdacs_alerts: list[dict[str, Any]],
    now: str,
) -> SignalRecord:
    """Compute E-class SignalRecord for a single Region."""
    w_score, w_detail = _score_weather(weather_data)
    q_score, q_detail = _score_earthquake(region, quakes)
    g_score, g_detail = _score_gdacs(region, gdacs_alerts)

    total = min(max(w_score, q_score, g_score), MAX_SCORE)
    raw_data = {
        "weather": w_detail,
        "earthquake": q_detail,
        "gdacs": g_detail,
        "sub_scores": {"weather": w_score, "earthquake": q_score, "gdacs": g_score},
    }
    logger.info(
        "Region %s E-score: %d %s",
        region.code, total, w_detail.get("alerts", []),
    )
    return SignalRecord(
        region=region.code,
        signal_class=SignalClass.E,
        score=total,
        raw_data=raw_data,
        source="open-meteo+usgs+gdacs",
        collected_at=now,
    )


# ── Main handler ──

def collect_weather_signals() -> list[SignalRecord]:
    """Collect E-class signals for all Regions. Returns list of records."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Shared data fetched once before per-region processing
    quakes = _fetch_earthquakes()
    gdacs_alerts = _fetch_gdacs()

    # Batch-fetch weather for all regions in a single HTTP request
    weather_batch: list[dict[str, Any]] = []
    try:
        weather_batch = _fetch_all_weather(ALL_REGIONS)
    except Exception as exc:
        logger.warning("Open-Meteo batch fetch failed: %s — using empty data", exc)
        weather_batch = []

    # Pad with empty dicts if the batch response is shorter than expected
    while len(weather_batch) < len(ALL_REGIONS):
        weather_batch.append({})

    records: list[SignalRecord] = []

    # Per-region scoring is CPU-bound; run concurrently to keep Lambda warm
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_region = {
            executor.submit(
                _process_region,
                region,
                weather_batch[i],
                quakes,
                gdacs_alerts,
                now,
            ): region
            for i, region in enumerate(ALL_REGIONS)
        }
        for future in as_completed(future_to_region):
            region = future_to_region[future]
            try:
                records.append(future.result())
            except Exception as exc:
                logger.error("Failed to collect weather for %s: %s", region.code, exc)
                records.append(SignalRecord(
                    region=region.code,
                    signal_class=SignalClass.E,
                    score=0,
                    raw_data={"error": str(exc)},
                    source="open-meteo+usgs+gdacs",
                    collected_at=now,
                ))

    return records


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point."""
    records = collect_weather_signals()

    # Write to DynamoDB
    written = 0
    for r in records:
        try:
            put_signal(r)
            written += 1
        except Exception as exc:
            logger.error("Failed to write signal for %s: %s", r.region, exc)

    return {
        "statusCode": 200,
        "body": {
            "collected": len(records),
            "written": written,
            "signal_class": "E",
        },
    }
