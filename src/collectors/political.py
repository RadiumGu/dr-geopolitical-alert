"""C-class signal collector: Political stability / travel advisories.

Data sources:
  - US State Department travel advisory RSS feed (primary)
    https://travel.state.gov/content/travel/en/traveladvisories/RSS.xml
  - Static country→level baseline as fallback

Advisory level → score mapping:
  Level 1 (Normal)         → 0
  Level 2 (Exercise caution) → 3
  Level 3 (Reconsider travel) → 8
  Level 4 (Do not travel)  → 15

Scoring (0-15): direct mapping from highest advisory level for the country.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from shared.types import SignalClass, SignalRecord
from shared.region_config import ALL_REGIONS, RegionConfig
from shared.db import put_signal
from shared.http_client import get_text

logger = logging.getLogger(__name__)

MAX_SCORE = 15
_ADVISORY_RSS = "https://travel.state.gov/content/travel/en/traveladvisories/RSS.xml"

# Static fallback: ISO-2 → advisory level (last-resort baseline)
_STATIC_LEVELS: dict[str, int] = {
    "IL": 4, "BH": 3, "AE": 2, "ZA": 2,
    "HK": 2, "IN": 2, "ID": 2, "TH": 2,
    "BR": 2, "MX": 3, "AU": 1, "NZ": 1,
    "MY": 1, "IT": 1, "ES": 1, "JP": 1,
    "KR": 2, "US": 1, "DE": 1, "GB": 1,
    "FR": 1, "SE": 1, "IE": 1, "SG": 1,
    "CH": 1, "CA": 1,
}

_LEVEL_SCORE: dict[int, int] = {1: 0, 2: 3, 3: 8, 4: 15}

# ISO-2 → country name fragment (for RSS title matching)
_COUNTRY_NAMES: dict[str, list[str]] = {
    "IL": ["Israel"],
    "BH": ["Bahrain"],
    "AE": ["United Arab Emirates", "UAE"],
    "ZA": ["South Africa"],
    "HK": ["Hong Kong"],
    "IN": ["India"],
    "ID": ["Indonesia"],
    "TH": ["Thailand"],
    "BR": ["Brazil"],
    "MX": ["Mexico"],
    "AU": ["Australia"],
    "NZ": ["New Zealand"],
    "MY": ["Malaysia"],
    "IT": ["Italy"],
    "ES": ["Spain"],
    "JP": ["Japan"],
    "KR": ["Korea"],
    "US": ["United States"],
    "DE": ["Germany"],
    "GB": ["United Kingdom", "Great Britain"],
    "FR": ["France"],
    "SE": ["Sweden"],
    "IE": ["Ireland"],
    "SG": ["Singapore"],
    "CH": ["Switzerland"],
    "CA": ["Canada"],
}


def _parse_rss_levels(xml_text: str) -> dict[str, int]:
    """Parse State Dept RSS to extract advisory level per country.

    Title format: "<Country> - Level N: <description>"
    Returns {country_name_fragment: level_int}.
    """
    levels: dict[str, int] = {}
    # Each item has <title>Country Name - Level N: ...</title>
    pattern = re.compile(r"<title><!\[CDATA\[([^\]]+) - Level (\d+):", re.IGNORECASE)
    for m in pattern.finditer(xml_text):
        country_name = m.group(1).strip()
        level = int(m.group(2))
        if 1 <= level <= 4:
            levels[country_name] = level
    return levels


def _iso2_to_level(iso2: str, rss_levels: dict[str, int]) -> tuple[int, str]:
    """Resolve advisory level for an ISO-2 country. Returns (level, source)."""
    names = _COUNTRY_NAMES.get(iso2, [])
    for name in names:
        if name in rss_levels:
            return rss_levels[name], "state_dept_rss"

    # Partial match
    for rss_name, level in rss_levels.items():
        if any(name.lower() in rss_name.lower() or rss_name.lower() in name.lower()
               for name in names):
            return level, "state_dept_rss_partial"

    static_level = _STATIC_LEVELS.get(iso2, 1)
    return static_level, "static_fallback"


def collect_political_signals() -> list[SignalRecord]:
    """Collect C-class signals for all Regions. Returns list of records."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rss_levels: dict[str, int] = {}
    rss_source = "static_fallback"

    try:
        xml_text = get_text(_ADVISORY_RSS)
        rss_levels = _parse_rss_levels(xml_text)
        rss_source = "state_dept_rss"
        logger.info("State Dept RSS: %d country advisories parsed", len(rss_levels))
    except Exception as exc:
        logger.warning("State Dept RSS fetch failed: %s — using static levels", exc)

    def _score_region(region: RegionConfig) -> SignalRecord:
        iso2 = region.country
        level, source = _iso2_to_level(iso2, rss_levels)
        score = min(_LEVEL_SCORE.get(level, 0), MAX_SCORE)
        logger.info("Region %s C-score: %d (level=%d, src=%s)", region.code, score, level, source)
        return SignalRecord(
            region=region.code,
            signal_class=SignalClass.C,
            score=score,
            raw_data={"iso2": iso2, "advisory_level": level, "source": source},
            source=rss_source,
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
                logger.error("Failed to collect political for %s: %s", region.code, exc)
                records.append(SignalRecord(
                    region=region.code,
                    signal_class=SignalClass.C,
                    score=0,
                    raw_data={"error": str(exc)},
                    source="state_dept_rss",
                    collected_at=now,
                ))

    return records


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point."""
    records = collect_political_signals()

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
            "signal_class": "C",
        },
    }
