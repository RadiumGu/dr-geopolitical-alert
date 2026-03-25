"""F-class signal collector: Compliance / regulatory / sanctions.

Data sources:
  - US Treasury OFAC RSS feed: https://home.treasury.gov/rss.xml
  - EU Official Journal RSS (EUR-Lex): recent restrictive-measure publications
    https://eur-lex.europa.eu/oj/annex-rss.xml
    (Filtered for "sanctions" / "restrictive measures" keywords)

Scoring (0-10):
  Sanctions hits for target country in last 7 days (OFAC + EU combined):
    >= 3 distinct actions  → 8-10
    1-2 actions            → 4-7
    keyword match only     → 2-3
    no match               → 0

  Countries under active comprehensive sanctions (static):
    Russia, North Korea, Iran, Cuba, Syria → baseline floor of 8
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

MAX_SCORE = 10
_OFAC_RSS_URL = "https://home.treasury.gov/rss.xml"
# EUR-Lex Official Journal annex RSS — contains CFSP/restrictive-measure decisions
_EU_OJ_RSS_URL = "https://eur-lex.europa.eu/oj/annex-rss.xml"
# Only keep EU OJ items that mention sanctions / restrictive measures
_EU_SANCTIONS_KEYWORDS = ["sanctions", "restrictive measures", "asset freeze", "travel ban"]

# Countries under comprehensive sanctions — static floor score
_SANCTIONED_BASELINE: dict[str, int] = {
    "RU": 9,  # Russia
    "KP": 10, # North Korea
    "IR": 10, # Iran
    "CU": 7,  # Cuba
    "SY": 9,  # Syria
    "BY": 6,  # Belarus
    "MM": 5,  # Myanmar
    "SD": 5,  # Sudan
    "ZW": 4,  # Zimbabwe
    "VE": 4,  # Venezuela
}

# Mapping ISO-2 → country name fragments for RSS keyword matching
_COUNTRY_KEYWORDS: dict[str, list[str]] = {
    "IL": ["Israel"],
    "BH": ["Bahrain"],
    "AE": ["UAE", "United Arab Emirates", "Emirati"],
    "ZA": ["South Africa"],
    "HK": ["Hong Kong"],
    "IN": ["India", "Indian"],
    "ID": ["Indonesia", "Indonesian"],
    "TH": ["Thailand", "Thai"],
    "BR": ["Brazil", "Brazilian"],
    "MX": ["Mexico", "Mexican"],
    "MY": ["Malaysia", "Malaysian"],
    "IT": ["Italy", "Italian"],
    "ES": ["Spain", "Spanish"],
    "JP": ["Japan", "Japanese"],
    "KR": ["South Korea", "Korean"],
    "US": ["United States", "American"],
    "DE": ["Germany", "German"],
    "GB": ["United Kingdom", "British", "UK"],
    "FR": ["France", "French"],
    "SE": ["Sweden", "Swedish"],
    "IE": ["Ireland", "Irish"],
    "SG": ["Singapore", "Singaporean"],
    "CH": ["Switzerland", "Swiss"],
    "CA": ["Canada", "Canadian"],
    "AU": ["Australia", "Australian"],
    "NZ": ["New Zealand"],
    "RU": ["Russia", "Russian"],
    "CN": ["China", "Chinese"],
}


def _fetch_eu_oj_items() -> list[dict[str, str]]:
    """Fetch EU Official Journal RSS and return items related to sanctions.

    Filters items whose title or description mentions sanctions-related
    keywords so that only relevant CFSP/restrictive-measure items are
    forwarded to country scoring.
    """
    try:
        xml_text = get_text(_EU_OJ_RSS_URL)
        all_items = _parse_rss_items(xml_text)
        filtered = [
            item for item in all_items
            if any(
                kw in (item["title"] + " " + item["description"]).lower()
                for kw in _EU_SANCTIONS_KEYWORDS
            )
        ]
        logger.info("EU OJ RSS: %d total items, %d sanctions-related", len(all_items), len(filtered))
        return filtered
    except Exception as exc:
        logger.warning("EU OJ RSS fetch failed: %s — skipping EU source", exc)
        return []


def _parse_rss_items(xml_text: str) -> list[dict[str, str]]:
    """Extract title + description from RSS <item> elements."""
    items: list[dict[str, str]] = []
    item_pattern = re.compile(r"<item>(.*?)</item>", re.DOTALL)
    title_pattern = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.DOTALL)
    desc_pattern = re.compile(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", re.DOTALL)

    for item_m in item_pattern.finditer(xml_text):
        item_text = item_m.group(1)
        title_m = title_pattern.search(item_text)
        desc_m = desc_pattern.search(item_text)
        items.append({
            "title": title_m.group(1).strip() if title_m else "",
            "description": desc_m.group(1).strip() if desc_m else "",
        })
    return items


def _count_hits(iso2: str, items: list[dict[str, str]]) -> int:
    """Count RSS items mentioning the target country."""
    keywords = _COUNTRY_KEYWORDS.get(iso2, [])
    if not keywords:
        return 0

    hits = 0
    for item in items:
        text = (item["title"] + " " + item["description"]).lower()
        if any(kw.lower() in text for kw in keywords):
            hits += 1
    return hits


def _sanctions_score(iso2: str, hits: int) -> tuple[int, str]:
    """Compute F-class score. Returns (score, reason)."""
    baseline = _SANCTIONED_BASELINE.get(iso2, 0)

    if hits >= 3:
        dynamic = min(8 + (hits - 3), MAX_SCORE)
    elif hits >= 1:
        dynamic = 4 + min(hits - 1, 3)
    else:
        dynamic = 0

    score = max(baseline, dynamic)
    reason = "sanctioned_country" if baseline > dynamic else "rss_hits"
    return min(score, MAX_SCORE), reason


def collect_compliance_signals() -> list[SignalRecord]:
    """Collect F-class signals for all Regions. Returns list of records."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rss_items: list[dict[str, str]] = []
    rss_ok = False
    eu_items: list[dict[str, str]] = []
    eu_ok = False

    try:
        xml_text = get_text(_OFAC_RSS_URL)
        rss_items = _parse_rss_items(xml_text)
        rss_ok = True
        logger.info("Treasury RSS: %d items", len(rss_items))
    except Exception as exc:
        logger.warning("Treasury RSS fetch failed: %s — using static scores only", exc)

    try:
        eu_items = _fetch_eu_oj_items()
    except Exception as exc:
        logger.warning("EU OJ RSS fetch failed: %s — skipping EU data", exc)
        eu_items = []
    eu_ok = len(eu_items) > 0

    def _score_region(region: RegionConfig) -> SignalRecord:
        iso2 = region.country
        hits_ofac = _count_hits(iso2, rss_items)
        hits_eu = _count_hits(iso2, eu_items)
        hits = hits_ofac + hits_eu
        score, reason = _sanctions_score(iso2, hits)
        logger.info("Region %s F-score: %d (ofac=%d, eu=%d, reason=%s)", region.code, score, hits_ofac, hits_eu, reason)
        return SignalRecord(
            region=region.code,
            signal_class=SignalClass.F,
            score=score,
            raw_data={
                "iso2": iso2,
                "rss_hits": hits,
                "ofac_hits": hits_ofac,
                "eu_hits": hits_eu,
                "sanctioned_baseline": _SANCTIONED_BASELINE.get(iso2, 0),
                "score_reason": reason,
                "rss_available": rss_ok,
                "eu_rss_available": eu_ok,
            },
            source="ofac_rss+eu_oj",
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
                logger.error("Failed to collect compliance for %s: %s", region.code, exc)
                records.append(SignalRecord(
                    region=region.code,
                    signal_class=SignalClass.F,
                    score=0,
                    raw_data={"error": str(exc)},
                    source="ofac_rss+eu_oj",
                    collected_at=now,
                ))

    return records


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point."""
    records = collect_compliance_signals()

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
            "signal_class": "F",
        },
    }
