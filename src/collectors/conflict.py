"""A-class signal collector: Armed conflict events.

Data sources:
  - UCDP GED API (primary, free): gedevents/25.0 — last 90 days
  - ACLED API (optional, key required): api.acleddata.com/acled/read

Scoring (0-20):
  Anomaly ratio = 7-day event count / 90-day daily average
    ratio >= 3.0  → 15-20 (weighted by event count)
    ratio 1.5-3.0 → 6-10
    ratio < 1.5   → 0-5
  Country with no history → 0
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.types import SignalClass, SignalRecord
from shared.region_config import ALL_REGIONS, COUNTRY_TO_REGIONS, RegionConfig
from shared.db import put_signal
from shared.http_client import get_json

logger = logging.getLogger(__name__)

MAX_SCORE = 20
_UCDP_URL = "https://ucdpapi.pcr.uu.se/api/gedevents/25.0"
_ACLED_URL = "https://api.acleddata.com/acled/read"

# Neighbor spillover map: {country_iso2: [(neighbor_iso2, distance_km), ...]}
# decay_factor = max(0, 1 - distance_km / 1000); at 1000 km decay reaches 0
NEIGHBOR_MAP: dict[str, list[tuple[str, int]]] = {
    "IL": [("SY", 100), ("LB", 50), ("IR", 1500), ("PS", 0), ("JO", 100)],
    "AE": [("YE", 500), ("IR", 300), ("IQ", 800), ("SA", 200), ("OM", 300)],
    "BH": [("IR", 200), ("SA", 50), ("IQ", 600), ("YE", 1000)],
    "IN": [("PK", 300), ("CN", 500)],
    "KR": [("KP", 50)],
    "HK": [("CN", 20)],
    "TH": [("MM", 300)],
    "MY": [("MM", 500)],
    "SG": [("MY", 10), ("ID", 20)],
    "TR": [("SY", 100), ("IR", 500), ("IQ", 400)],
    "GR": [("TR", 300)],
    "SA": [("YE", 300), ("IR", 800), ("IQ", 600)],
}


def _fetch_ucdp_events(days: int = 90) -> list[dict[str, Any]]:
    """Fetch UCDP GED conflict events for the last N days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    token = os.environ.get("UCDP_ACCESS_TOKEN", "")
    headers = {"x-ucdp-access-token": token} if token else None
    data = get_json(
        _UCDP_URL,
        params={"pagesize": 1000, "StartDate": since},
        headers=headers,
    )
    return data.get("Result", [])


def _fetch_acled_public(days: int = 30) -> list[dict[str, Any]]:
    """Fetch recent ACLED events via the public no-auth endpoint (last 30 days, max 500)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    data = get_json(
        _ACLED_URL,
        params={"limit": 500, "event_date": since, "event_date_where": ">="},
    )
    return data.get("data", [])


def _fetch_acled_events(days: int = 90) -> list[dict[str, Any]]:
    """Fetch ACLED conflict events (requires ACLED_API_KEY env var)."""
    api_key = os.environ.get("ACLED_API_KEY", "")
    email = os.environ.get("ACLED_EMAIL", "")
    if not api_key or not email:
        raise ValueError("ACLED_API_KEY / ACLED_EMAIL not set")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    data = get_json(
        _ACLED_URL,
        params={
            "key": api_key,
            "email": email,
            "event_date": since,
            "event_date_where": ">=",
            "limit": 5000,
        },
    )
    return data.get("data", [])


def _build_country_timeseries(
    events: list[dict[str, Any]], source: str
) -> dict[str, list[str]]:
    """Return {iso2_country: [date_str, ...]} from raw events.

    Supports both UCDP (country_id ISO2) and ACLED (iso field / country) schemas.
    """
    series: dict[str, list[str]] = defaultdict(list)
    for ev in events:
        if source == "ucdp":
            iso2 = str(ev.get("country_id", "")).strip().upper()
            date = str(ev.get("date_start", ""))[:10]
        else:  # acled
            iso2 = str(ev.get("iso", ev.get("country", ""))).strip().upper()
            date = str(ev.get("event_date", ""))[:10]
        if iso2 and date:
            series[iso2].append(date)
    return series


def _anomaly_score(count_7d: int, daily_avg_90d: float) -> int:
    """Compute A-class score from recent count vs historical average."""
    if daily_avg_90d == 0:
        return min(count_7d * 2, 10)  # no history — low-confidence score

    ratio = count_7d / (daily_avg_90d * 7)

    if ratio >= 3.0:
        base = 15
        extra = min(int((ratio - 3.0) * 2), 5)
        return base + extra
    elif ratio >= 1.5:
        return 6 + min(int((ratio - 1.5) * 2.7), 4)
    else:
        return min(int(ratio * 3.3), 5)


def collect_conflict_signals() -> list[SignalRecord]:
    """Collect A-class signals for all Regions. Returns list of records."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.now(timezone.utc).date()

    # Try keyed ACLED first, fall back to UCDP, then public ACLED
    source_label = "acled"
    try:
        events = _fetch_acled_events(90)
        logger.info("Using keyed ACLED: %d events", len(events))
    except Exception as exc:
        logger.info("Keyed ACLED unavailable (%s), falling back to UCDP", exc)
        source_label = "ucdp"
        try:
            events = _fetch_ucdp_events(90)
            logger.info("Using UCDP: %d events", len(events))
        except Exception as exc2:
            logger.info("UCDP unavailable (%s), falling back to public ACLED", exc2)
            source_label = "acled_public"
            try:
                events = _fetch_acled_public(30)
                logger.info("Using public ACLED: %d events", len(events))
            except Exception as exc3:
                logger.error("All conflict sources failed: %s", exc3)
                events = []

    country_series = _build_country_timeseries(events, source_label)

    def _score_region(region: RegionConfig) -> SignalRecord:
        iso2 = region.country
        dates = country_series.get(iso2, [])

        cutoff_7d = (today - timedelta(days=7)).isoformat()
        count_7d = sum(1 for d in dates if d >= cutoff_7d)
        daily_avg_90d = len(dates) / 90.0

        own_score = min(_anomaly_score(count_7d, daily_avg_90d), MAX_SCORE)

        # Neighbor spillover: check conflict in adjacent countries
        spillover_details: list[dict[str, Any]] = []
        spillover_scores: list[float] = []
        for neighbor_iso2, distance_km in NEIGHBOR_MAP.get(iso2, []):
            n_dates = country_series.get(neighbor_iso2, [])
            n_count_7d = sum(1 for d in n_dates if d >= cutoff_7d)
            n_daily_avg = len(n_dates) / 90.0
            n_score = min(_anomaly_score(n_count_7d, n_daily_avg), MAX_SCORE)
            decay = max(0.0, 1.0 - distance_km / 1000.0)
            spill = n_score * decay
            if spill > 0:
                spillover_scores.append(spill)
                spillover_details.append({
                    "neighbor": neighbor_iso2,
                    "distance_km": distance_km,
                    "neighbor_score": n_score,
                    "decay": round(decay, 3),
                    "spillover": round(spill, 2),
                })

        max_spillover = max(spillover_scores, default=0.0)
        score = min(max(own_score, int(max_spillover)), MAX_SCORE)

        raw_data: dict[str, Any] = {
            "iso2": iso2,
            "total_90d": len(dates),
            "count_7d": count_7d,
            "daily_avg_90d": round(daily_avg_90d, 3),
            "anomaly_ratio": round(count_7d / max(daily_avg_90d * 7, 0.001), 2),
            "own_score": own_score,
        }
        if spillover_details:
            raw_data["spillover"] = spillover_details

        logger.info(
            "Region %s A-score: %d (own=%d, spillover=%.1f, 7d=%d, avg=%.2f)",
            region.code, score, own_score, max_spillover, count_7d, daily_avg_90d,
        )
        return SignalRecord(
            region=region.code,
            signal_class=SignalClass.A,
            score=score,
            raw_data=raw_data,
            source=source_label,
            collected_at=now,
        )

    records: list[SignalRecord] = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_region = {executor.submit(_score_region, r): r for r in ALL_REGIONS}
        for future in as_completed(future_to_region):
            region = future_to_region[future]
            try:
                records.append(future.result())
            except Exception as exc:
                logger.error("Failed to collect conflict for %s: %s", region.code, exc)
                records.append(SignalRecord(
                    region=region.code,
                    signal_class=SignalClass.A,
                    score=0,
                    raw_data={"error": str(exc)},
                    source=source_label,
                    collected_at=now,
                ))

    return records


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point."""
    records = collect_conflict_signals()

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
            "signal_class": "A",
        },
    }
