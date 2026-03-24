"""D-class signal collector: Physical infrastructure / submarine cable disruptions.

Data sources:
  - GDELT GKG v2 news search: keywords related to submarine cable cuts/outages
    https://api.gdeltproject.org/api/v2/doc/doc (free, no key)

Scoring (0-10):
  News-based cable threat detection for Region's associated cables:
    confirmed cut/outage in last 24h    → 8-10
    reported incident / disruption      → 4-7
    no relevant news                    → 0

  TODO: Integrate live cable status APIs (SubmarineCableMap, TeleGeography)
        when they become publicly available.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from shared.types import SignalClass, SignalRecord
from shared.region_config import ALL_REGIONS
from shared.db import put_signal
from shared.http_client import get_json

logger = logging.getLogger(__name__)

MAX_SCORE = 10
_GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# Keywords that indicate a cable disruption event
_CABLE_INCIDENT_KEYWORDS = [
    "submarine cable cut",
    "undersea cable damaged",
    "submarine cable outage",
    "cable disruption",
    "cable severed",
    "internet cable",
]

# High-severity terms that push toward max score
_CONFIRMED_TERMS = {"cut", "severed", "damaged", "outage"}
_REPORTED_TERMS = {"disruption", "incident", "suspected", "reported"}


def _fetch_cable_news(cable_name: str) -> list[dict[str, Any]]:
    """Search GDELT for recent news about a specific submarine cable."""
    query = f'"{cable_name}" cable (cut OR damage OR outage OR disruption)'
    try:
        data = get_json(
            _GDELT_DOC_URL,
            params={
                "query": query,
                "mode": "artlist",
                "maxrecords": 10,
                "timespan": "24h",
                "format": "json",
            },
        )
        return data.get("articles", [])
    except Exception as exc:
        logger.warning("GDELT cable news fetch failed for %s: %s", cable_name, exc)
        return []


def _score_cable_news(articles: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Score cable news articles. Returns (score, relevant_titles)."""
    if not articles:
        return 0, []

    max_score = 0
    relevant_titles: list[str] = []

    for art in articles[:5]:
        title = art.get("title", "").lower()
        url = art.get("url", "")
        if not title:
            continue

        if any(term in title for term in _CONFIRMED_TERMS):
            max_score = max(max_score, 8)
            relevant_titles.append(art.get("title", ""))
        elif any(term in title for term in _REPORTED_TERMS):
            max_score = max(max_score, 4)
            relevant_titles.append(art.get("title", ""))

    return min(max_score, MAX_SCORE), relevant_titles


def collect_infrastructure_signals() -> list[SignalRecord]:
    """Collect D-class signals for all Regions. Returns list of records."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Cache GDELT results per cable to avoid duplicate fetches
    cable_scores: dict[str, tuple[int, list[str]]] = {}

    records: list[SignalRecord] = []

    for region in ALL_REGIONS:
        try:
            cables = region.cables
            region_score = 0
            all_titles: list[str] = []
            cable_detail: dict[str, Any] = {}

            for cable in cables:
                if cable not in cable_scores:
                    articles = _fetch_cable_news(cable)
                    cable_scores[cable] = _score_cable_news(articles)

                c_score, c_titles = cable_scores[cable]
                if c_score > 0:
                    cable_detail[cable] = {"score": c_score, "headlines": c_titles[:2]}
                    all_titles.extend(c_titles)
                region_score = max(region_score, c_score)

            region_score = min(region_score, MAX_SCORE)

            records.append(SignalRecord(
                region=region.code,
                signal_class=SignalClass.D,
                score=region_score,
                raw_data={
                    "cables_monitored": cables,
                    "cable_incidents": cable_detail,
                    "relevant_headlines": all_titles[:3],
                },
                source="gdelt",
                collected_at=now,
            ))
            logger.info("Region %s D-score: %d (cables=%s)", region.code, region_score, cables)

        except Exception as exc:
            logger.error("Failed to collect infrastructure for %s: %s", region.code, exc)
            records.append(SignalRecord(
                region=region.code,
                signal_class=SignalClass.D,
                score=0,
                raw_data={"error": str(exc)},
                source="gdelt",
                collected_at=now,
            ))

    return records


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point."""
    records = collect_infrastructure_signals()

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
            "signal_class": "D",
        },
    }
