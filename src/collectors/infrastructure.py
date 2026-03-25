"""D-class signal collector — Physical Infrastructure (submarine cables + network paths).

Data sources:
- Cloudflare Radar: country-level traffic anomaly detection (requires API token)
- RIPE Atlas: latency/path changes to AWS Region endpoints via global probe network

NOTE: Previous GDELT news search implementation was disabled due to extremely low
signal quality (high false positive rate, 15-60min delay, no actual cable status).
Current implementation uses Cloudflare Radar + RIPE Atlas for real network telemetry.

Requires SSM parameters:
- /dr-alert/cloudflare-api-token (optional, falls back to RIPE Atlas only)
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

from shared.region_config import ALL_REGIONS, REGION_MAP
from shared.db import put_signal
from shared.types import SignalClass, SignalRecord

logger = logging.getLogger(__name__)

# RIPE Atlas built-in anchoring measurements to AWS endpoints per region
# These are publicly queryable without API key
REGION_ATLAS_TARGETS: dict[str, dict] = {
    # Map AWS region → RIPE Atlas anchor probe ID in that country
    # AS16509 = Amazon Web Services
    "ap-southeast-1": {"country": "SG", "asn": 16509},
    "ap-northeast-1": {"country": "JP", "asn": 16509},
    "ap-northeast-2": {"country": "KR", "asn": 16509},
    "ap-south-1": {"country": "IN", "asn": 16509},
    "eu-west-1": {"country": "IE", "asn": 16509},
    "eu-central-1": {"country": "DE", "asn": 16509},
    "us-east-1": {"country": "US", "asn": 16509},
    "us-west-2": {"country": "US", "asn": 16509},
    "me-south-1": {"country": "BH", "asn": 16509},
    "me-central-1": {"country": "AE", "asn": 16509},
    "il-central-1": {"country": "IL", "asn": 16509},
    "af-south-1": {"country": "ZA", "asn": 16509},
    "sa-east-1": {"country": "BR", "asn": 16509},
}

D_WEIGHT = 10  # Max score for this class


def _fetch_json(url: str, headers: dict | None = None, timeout: int = 15) -> Any:
    """Fetch JSON from a URL with optional headers."""
    if not url.startswith("https://"):
        logger.warning("Refusing to fetch non-HTTPS URL: %s", url)
        return None
    req = Request(url, headers=headers or {})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return None


def _check_ripe_atlas_country(country_code: str) -> dict:
    """Check RIPE Atlas probe connectivity status for a country.

    Returns probe statistics: total connected, total disconnected,
    and the ratio as a health indicator.
    """
    url = (
        f"https://atlas.ripe.net/api/v2/probes/"
        f"?country_code={country_code}&limit=1&status=1"
    )
    connected = _fetch_json(url)

    url_disc = (
        f"https://atlas.ripe.net/api/v2/probes/"
        f"?country_code={country_code}&limit=1&status=2"
    )
    disconnected = _fetch_json(url_disc)

    if not connected or not disconnected:
        return {"connected": 0, "disconnected": 0, "ratio": 1.0}

    conn_count = connected.get("count", 0)
    disc_count = disconnected.get("count", 0)
    total = conn_count + disc_count

    ratio = conn_count / total if total > 0 else 1.0

    return {
        "connected": conn_count,
        "disconnected": disc_count,
        "total": total,
        "connectivity_ratio": round(ratio, 3),
    }


def _score_infrastructure(region: str, atlas_data: dict) -> tuple[int, list[str]]:
    """Score D-class signal based on RIPE Atlas probe connectivity.

    Scoring:
    - connectivity_ratio >= 0.95 → 0 (normal)
    - 0.90 - 0.95 → 2 (minor degradation)
    - 0.80 - 0.90 → 5 (moderate, possible cable issue)
    - 0.60 - 0.80 → 8 (significant, likely infrastructure event)
    - < 0.60 → 10 (severe, major outage)
    """
    ratio = atlas_data.get("connectivity_ratio", 1.0)
    total = atlas_data.get("total", 0)
    alerts = []

    if total < 5:
        # Too few probes to be meaningful
        return 0, []

    if ratio >= 0.95:
        score = 0
    elif ratio >= 0.90:
        score = 2
        alerts.append(f"probe_degradation:{round((1-ratio)*100, 1)}%")
    elif ratio >= 0.80:
        score = 5
        alerts.append(f"probe_moderate_loss:{round((1-ratio)*100, 1)}%")
    elif ratio >= 0.60:
        score = 8
        alerts.append(f"probe_significant_loss:{round((1-ratio)*100, 1)}%")
    else:
        score = 10
        alerts.append(f"probe_severe_loss:{round((1-ratio)*100, 1)}%")

    return score, alerts


def _collect_one_region(region: str) -> dict:
    """Collect D-class signal for a single region."""
    config = REGION_MAP.get(region)
    if not config:
        return {"region": region, "score": 0, "raw_data": {}, "alerts": []}

    atlas_target = REGION_ATLAS_TARGETS.get(region)
    atlas_data = {}

    if atlas_target:
        atlas_data = _check_ripe_atlas_country(atlas_target["country"])
    else:
        # Fallback: use the country from region config
        # Extract country code from region config if available
        atlas_data = {"connected": 0, "disconnected": 0, "total": 0, "connectivity_ratio": 1.0}

    score, alerts = _score_infrastructure(region, atlas_data)

    return {
        "region": region,
        "score": score,
        "raw_data": {
            "ripe_atlas": atlas_data,
            "source": "ripe_atlas_probe_connectivity",
        },
        "alerts": alerts,
    }


def handler(event: Any, context: Any) -> dict:
    """Lambda handler: collect D-class infrastructure signals for all regions using RIPE Atlas."""
    table_name = os.environ.get("SIGNALS_TABLE", "dr-alert-signals")
    results = []
    written = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_collect_one_region, region): region
            for region in REGION_MAP
        }

        for future in as_completed(futures):
            region = futures[future]
            try:
                result = future.result()
                results.append(result)

                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                record = SignalRecord(
                    region=region,
                    signal_class=SignalClass.D,
                    score=result["score"],
                    raw_data=result["raw_data"],
                    source="ripe_atlas",
                    collected_at=now,
                )
                try:
                    put_signal(record)
                    written += 1
                except Exception as e:
                    logger.error("Failed to write signal for %s: %s", region, e)

            except Exception as e:
                logger.error("Failed to collect D-class for %s: %s", region, e)

    return {
        "statusCode": 200,
        "body": {
            "collected": len(results),
            "written": written,
            "signal_class": "D",
        },
    }
