"""B-class signal collector: Cyber threats.

Data sources (free, no API key):
  - abuse.ch Feodo Tracker: C2 IP blocklist with country attribution
  - abuse.ch URLhaus: recent malicious URLs with country/host info

Scoring (0-15):
  Threat density = threat count for target country's IP space (last 24h)
    >= 50 threats   → 12-15
    10-49 threats   → 6-11
    1-9 threats     → 1-5
    0               → 0
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from shared.types import SignalClass, SignalRecord
from shared.region_config import ALL_REGIONS
from shared.db import put_signal
from shared.http_client import get_json, get_text

logger = logging.getLogger(__name__)

MAX_SCORE = 15
_FEODO_URL = "https://feodotracker.abuse.ch/downloads/ipblocklist.json"
_URLHAUS_URL = "https://urlhaus-api.abuse.ch/v1/urls/recent/"


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
        # URLhaus entries have 'country_code' or derive from host
        country = str(entry.get("country_code", "")).strip().upper()
        if country:
            counts[country] += 1

    return counts


def _density_score(threat_count: int) -> int:
    """Map threat count to B-class score."""
    if threat_count >= 50:
        return min(12 + (threat_count - 50) // 25, MAX_SCORE)
    elif threat_count >= 10:
        return 6 + min((threat_count - 10) // 8, 5)
    elif threat_count >= 1:
        return min(threat_count, 5)
    return 0


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

    records: list[SignalRecord] = []

    for region in ALL_REGIONS:
        try:
            iso2 = region.country
            count = threat_counts.get(iso2, 0)
            score = min(_density_score(count), MAX_SCORE)

            records.append(SignalRecord(
                region=region.code,
                signal_class=SignalClass.B,
                score=score,
                raw_data={
                    "iso2": iso2,
                    "threat_count": count,
                    "feodo_total": len(feodo),
                    "urlhaus_total": len(urlhaus),
                },
                source="feodotracker+urlhaus",
                collected_at=now,
            ))
            logger.info("Region %s B-score: %d (threats=%d)", region.code, score, count)

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
