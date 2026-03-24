"""G-class signal collector: BGP / internet backbone anomalies.

Data sources:
  - IODA API (Internet Outage Detection and Analysis, free):
    https://api.ioda.inetintel.cc.gatech.edu/v2/signals/raw
  - Cloudflare Radar (optional, requires CF_RADAR_TOKEN env var):
    https://api.cloudflare.com/client/v4/radar/bgp/route-leaks/events

Scoring (0-15):
  Active outage (IODA score drop >= 50%)  → 10-15
  Anomaly detected (score drop 20-50%)    → 5-9
  Minor deviation (< 20%)                 → 1-4
  Normal / no data                        → 0
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.types import SignalClass, SignalRecord
from shared.region_config import ALL_REGIONS
from shared.db import put_signal
from shared.http_client import get_json

logger = logging.getLogger(__name__)

MAX_SCORE = 15
_IODA_URL = "https://api.ioda.inetintel.cc.gatech.edu/v2/signals/raw"
_CF_RADAR_URL = "https://api.cloudflare.com/client/v4/radar/bgp/route-leaks/events"

# ISO-2 → IODA entity code (country codes used by IODA)
# IODA uses ISO 3166-1 alpha-2 directly as entity type=country
_IODA_ENTITY_TYPE = "country"


def _fetch_ioda_signals(iso2: str) -> dict[str, Any]:
    """Fetch IODA raw signals for a country.

    Returns the latest signal data with scores for BGP, UCSD telescope, and Ping.
    """
    now = datetime.now(timezone.utc)
    from_ts = int((now - timedelta(hours=2)).timestamp())
    until_ts = int(now.timestamp())

    data = get_json(
        _IODA_URL,
        params={
            "entityType": _IODA_ENTITY_TYPE,
            "entityCode": iso2.lower(),
            "from": from_ts,
            "until": until_ts,
            "datasource": "bgp,ucsd-nt,ping-slash24",
        },
    )
    return data


def _score_ioda(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Compute G-class score from IODA signal data.

    Returns (score, detail_dict).
    """
    detail: dict[str, Any] = {"sources": {}}

    if not data:
        return 0, detail

    # IODA response structure: {"data": [{datasource, values, ...}]}
    sources_data = data.get("data", [])
    if not sources_data:
        return 0, detail

    max_drop_pct = 0.0

    for source in sources_data:
        name = source.get("datasource", "unknown")
        values = source.get("values", [])
        if not values or len(values) < 2:
            continue

        # Values are (timestamp, score) pairs; last is most recent
        # Look at the last few values to detect drops
        recent = [v[1] for v in values[-6:] if v[1] is not None]
        if len(recent) < 2:
            continue

        baseline = max(recent[:-1])  # max of all but last
        current = recent[-1]

        if baseline == 0:
            continue

        drop_pct = max(0.0, (baseline - current) / baseline * 100)
        max_drop_pct = max(max_drop_pct, drop_pct)

        detail["sources"][name] = {
            "baseline": round(baseline, 2),
            "current": round(current, 2),
            "drop_pct": round(drop_pct, 1),
        }

    if max_drop_pct >= 50:
        score = 10 + min(int((max_drop_pct - 50) / 10), 5)
    elif max_drop_pct >= 20:
        score = 5 + int((max_drop_pct - 20) / 6)
    elif max_drop_pct >= 5:
        score = max(1, int(max_drop_pct / 5))
    else:
        score = 0

    detail["max_drop_pct"] = round(max_drop_pct, 1)
    return min(score, MAX_SCORE), detail


def _fetch_cf_radar_leaks(iso2: str) -> list[dict[str, Any]]:
    """Fetch Cloudflare Radar BGP route-leak events for a country (optional)."""
    token = os.environ.get("CF_RADAR_TOKEN", "")
    if not token:
        return []

    data = get_json(
        _CF_RADAR_URL,
        params={
            "involvedCountry": iso2,
            "dateRange": "1d",
            "limit": 10,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    return data.get("result", {}).get("events", [])


def _score_cf_leaks(leaks: list[dict[str, Any]]) -> int:
    """Add to score if Cloudflare detects BGP leaks for the country."""
    if not leaks:
        return 0
    return min(3 + len(leaks), 8)


def collect_bgp_signals() -> list[SignalRecord]:
    """Collect G-class signals for all Regions. Returns list of records."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Cache IODA results per ISO-2 to avoid redundant calls for same country
    ioda_cache: dict[str, tuple[int, dict[str, Any]]] = {}
    cf_cache: dict[str, int] = {}

    records: list[SignalRecord] = []

    for region in ALL_REGIONS:
        try:
            iso2 = region.country

            # IODA
            if iso2 not in ioda_cache:
                try:
                    ioda_data = _fetch_ioda_signals(iso2)
                    ioda_cache[iso2] = _score_ioda(ioda_data)
                except Exception as exc:
                    logger.warning("IODA fetch failed for %s: %s", iso2, exc)
                    ioda_cache[iso2] = (0, {"error": str(exc)})

            ioda_score, ioda_detail = ioda_cache[iso2]

            # Cloudflare (optional)
            if iso2 not in cf_cache:
                try:
                    leaks = _fetch_cf_radar_leaks(iso2)
                    cf_cache[iso2] = _score_cf_leaks(leaks)
                except Exception as exc:
                    logger.debug("CF Radar unavailable for %s: %s", iso2, exc)
                    cf_cache[iso2] = 0

            cf_score = cf_cache[iso2]

            # Combine: take the higher of the two sources
            score = min(max(ioda_score, cf_score), MAX_SCORE)

            records.append(SignalRecord(
                region=region.code,
                signal_class=SignalClass.G,
                score=score,
                raw_data={
                    "iso2": iso2,
                    "ioda": ioda_detail,
                    "cf_leak_score": cf_score,
                    "sub_scores": {"ioda": ioda_score, "cf": cf_score},
                },
                source="ioda+cf_radar",
                collected_at=now,
            ))
            logger.info("Region %s G-score: %d (ioda=%d, cf=%d)", region.code, score, ioda_score, cf_score)

        except Exception as exc:
            logger.error("Failed to collect BGP for %s: %s", region.code, exc)
            records.append(SignalRecord(
                region=region.code,
                signal_class=SignalClass.G,
                score=0,
                raw_data={"error": str(exc)},
                source="ioda+cf_radar",
                collected_at=now,
            ))

    return records


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point."""
    records = collect_bgp_signals()

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
            "signal_class": "G",
        },
    }
