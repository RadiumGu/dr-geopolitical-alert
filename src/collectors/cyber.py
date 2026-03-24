"""B-class signal collector: Cyber threats.

Data sources (free, no API key):
  - abuse.ch Feodo Tracker: C2 IP blocklist with country attribution
  - abuse.ch URLhaus: recent malicious URLs with country/host info

Scoring (0-15) — trend-based:
  Threat ratio = current_count / 24h_historical_baseline
    ratio >= 3.0  → 12-15  (sharp spike)
    ratio 1.5-3.0 → 6-11   (elevated)
    ratio 0.8-1.5 → 0-5    (near baseline)
    ratio < 0.8   → 0      (below baseline)

  When no historical baseline is available the score falls back to absolute
  density scoring (same thresholds as ratio-based, applied to raw counts).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from shared.types import SignalClass, SignalRecord
from shared.region_config import ALL_REGIONS, RegionConfig
from shared.db import put_signal, get_signal_history
from shared.http_client import get_json

logger = logging.getLogger(__name__)

MAX_SCORE = 15
_FEODO_URL = "https://feodotracker.abuse.ch/downloads/ipblocklist.json"
_URLHAUS_URL = "https://urlhaus-api.abuse.ch/v1/urls/recent/"

# Number of historical B-class records to use for baseline (≈ 24 h at 10-min cadence)
_HISTORY_LIMIT = 144


def _fetch_feodo() -> list[dict[str, Any]]:
    """Fetch Feodo Tracker C2 IP blocklist."""
    data = get_json(_FEODO_URL)
    return data if isinstance(data, list) else []


def _fetch_urlhaus() -> list[dict[str, Any]]:
    """Fetch recent URLhaus malicious URLs."""
    data = get_json(_URLHAUS_URL)
    return data.get("urls", []) if isinstance(data, dict) else []


def _count_threats_by_country(
    feodo: list[dict[str, Any]], urlhaus: list[dict[str, Any]]
) -> dict[str, int]:
    """Aggregate threat count per ISO-2 country code."""
    counts: dict[str, int] = defaultdict(int)

    for entry in feodo:
        country = str(entry.get("country", "")).strip().upper()
        if country:
            counts[country] += 1

    for entry in urlhaus:
        country = str(entry.get("country_code", "")).strip().upper()
        if country:
            counts[country] += 1

    return counts


def _absolute_score(threat_count: int) -> int:
    """Map absolute threat count to B-class score (fallback when no history)."""
    if threat_count >= 50:
        return min(12 + (threat_count - 50) // 25, MAX_SCORE)
    elif threat_count >= 10:
        return 6 + min((threat_count - 10) // 8, 5)
    elif threat_count >= 1:
        return min(threat_count, 5)
    return 0


def _trend_score(current_count: int, region_code: str) -> tuple[int, float]:
    """Score based on trend vs 24-hour historical baseline from DynamoDB.

    Compares current_count to the mean threat_count stored in the last
    _HISTORY_LIMIT B-class signal records for this region.

    Returns:
        (score, ratio) where ratio = current / baseline_avg.
        Falls back to (_absolute_score, 1.0) when no history is available.
    """
    history = get_signal_history(region_code, "B", limit=_HISTORY_LIMIT)
    if not history:
        return _absolute_score(current_count), 1.0

    historical_counts: list[float] = []
    for item in history:
        raw = item.get("raw_data", {})
        if isinstance(raw, dict) and "threat_count" in raw:
            historical_counts.append(float(raw["threat_count"]))

    if not historical_counts:
        return _absolute_score(current_count), 1.0

    baseline_avg = sum(historical_counts) / len(historical_counts)
    if baseline_avg == 0:
        # No historical threat activity — any non-zero count is notable
        return min(current_count * 2, MAX_SCORE), float("inf")

    ratio = current_count / baseline_avg

    if ratio >= 3.0:
        score = min(12 + int((ratio - 3.0) * 2), MAX_SCORE)
    elif ratio >= 1.5:
        score = 6 + min(int((ratio - 1.5) * 4), 5)
    elif ratio >= 0.8:
        score = max(0, int(ratio * 3))
    else:
        score = 0

    return min(score, MAX_SCORE), round(ratio, 2)


def _score_region(
    region: RegionConfig,
    threat_counts: dict[str, int],
    feodo_total: int,
    urlhaus_total: int,
    now: str,
) -> SignalRecord:
    """Compute B-class SignalRecord for a single Region using trend comparison."""
    iso2 = region.country
    current_count = threat_counts.get(iso2, 0)
    score, ratio = _trend_score(current_count, region.code)

    logger.info(
        "Region %s B-score: %d (count=%d, ratio=%.2f)",
        region.code, score, current_count, ratio,
    )
    return SignalRecord(
        region=region.code,
        signal_class=SignalClass.B,
        score=score,
        raw_data={
            "iso2": iso2,
            "threat_count": current_count,
            "trend_ratio": ratio,
            "feodo_total": feodo_total,
            "urlhaus_total": urlhaus_total,
        },
        source="feodotracker+urlhaus",
        collected_at=now,
    )


def collect_cyber_signals() -> list[SignalRecord]:
    """Collect B-class signals for all Regions. Returns list of records."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    feodo: list[dict[str, Any]] = []
    urlhaus: list[dict[str, Any]] = []

    try:
        feodo = _fetch_feodo()
        logger.info("Feodo: %d entries", len(feodo))
    except Exception as exc:
        logger.warning("Feodo fetch failed: %s", exc)

    try:
        urlhaus = _fetch_urlhaus()
        logger.info("URLhaus: %d entries", len(urlhaus))
    except Exception as exc:
        logger.warning("URLhaus fetch failed: %s", exc)

    threat_counts = _count_threats_by_country(feodo, urlhaus)
    feodo_total = len(feodo)
    urlhaus_total = len(urlhaus)

    records: list[SignalRecord] = []

    # Per-region scoring involves a DynamoDB read for trend history — run concurrently
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_region = {
            executor.submit(
                _score_region, region, threat_counts, feodo_total, urlhaus_total, now
            ): region
            for region in ALL_REGIONS
        }
        for future in as_completed(future_to_region):
            region = future_to_region[future]
            try:
                records.append(future.result())
            except Exception as exc:
                logger.error("Failed to collect cyber for %s: %s", region.code, exc)
                records.append(SignalRecord(
                    region=region.code,
                    signal_class=SignalClass.B,
                    score=0,
                    raw_data={"error": str(exc)},
                    source="feodotracker+urlhaus",
                    collected_at=now,
                ))

    return records


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point."""
    records = collect_cyber_signals()

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
            "signal_class": "B",
        },
    }
