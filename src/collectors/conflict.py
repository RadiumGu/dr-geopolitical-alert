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
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.types import SignalClass, SignalRecord
from shared.region_config import ALL_REGIONS, COUNTRY_TO_REGIONS
from shared.db import put_signal
from shared.http_client import get_json

logger = logging.getLogger(__name__)

MAX_SCORE = 20
_UCDP_URL = "https://ucdpapi.pcr.uu.se/api/gedevents/25.0"
_ACLED_URL = "https://api.acleddata.com/acled/read"


def _fetch_ucdp_events(days: int = 90) -> list[dict[str, Any]]:
    """Fetch UCDP GED conflict events for the last N days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    data = get_json(
        _UCDP_URL,
        params={"pagesize": 1000, "StartDate": since},
    )
    return data.get("Result", [])


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

    # Try ACLED first, fall back to UCDP
    source_label = "ucdp"
    try:
        events = _fetch_acled_events(90)
        source_label = "acled"
        logger.info("Using ACLED: %d events", len(events))
    except Exception as exc:
        logger.info("ACLED unavailable (%s), falling back to UCDP", exc)
        try:
            events = _fetch_ucdp_events(90)
            logger.info("Using UCDP: %d events", len(events))
        except Exception as exc2:
            logger.error("Both conflict sources failed: %s", exc2)
            events = []

    country_series = _build_country_timeseries(events, source_label)

    records: list[SignalRecord] = []

    for region in ALL_REGIONS:
        try:
            iso2 = region.country
            dates = country_series.get(iso2, [])

            # Count events in last 7 days vs 90-day daily average
            cutoff_7d = (today - timedelta(days=7)).isoformat()
            count_7d = sum(1 for d in dates if d >= cutoff_7d)
            daily_avg_90d = len(dates) / 90.0

            score = _anomaly_score(count_7d, daily_avg_90d)
            score = min(score, MAX_SCORE)

            raw_data = {
                "iso2": iso2,
                "total_90d": len(dates),
                "count_7d": count_7d,
                "daily_avg_90d": round(daily_avg_90d, 3),
                "anomaly_ratio": round(count_7d / max(daily_avg_90d * 7, 0.001), 2),
            }

            records.append(SignalRecord(
                region=region.code,
                signal_class=SignalClass.A,
                score=score,
                raw_data=raw_data,
                source=source_label,
                collected_at=now,
            ))
            logger.info("Region %s A-score: %d (7d=%d, avg=%.2f)", region.code, score, count_7d, daily_avg_90d)

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
