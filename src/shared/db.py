"""DynamoDB operations for signals and GPRI tables."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from .types import SignalClass, SignalRecord, GpriRecord, GpriLevel, gpri_to_level

_dynamodb = boto3.resource("dynamodb")

SIGNALS_TABLE = "dr-alert-signals"
GPRI_TABLE = "dr-alert-gpri"

TTL_SIGNALS_DAYS = 7
TTL_GPRI_DAYS = 90


def _ttl(days: int) -> int:
    return int(time.time()) + days * 86400


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _float_to_decimal(obj: Any) -> Any:
    """Recursively convert floats to Decimal for DynamoDB."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: _float_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_float_to_decimal(i) for i in obj]
    return obj


# ── Signals ──


def put_signal(record: SignalRecord) -> None:
    """Write a signal measurement to DynamoDB."""
    table = _dynamodb.Table(SIGNALS_TABLE)
    table.put_item(
        Item={
            "PK": record.pk,
            "SK": record.sk,
            "signal_class": record.signal_class.value,
            "score": record.score,
            "raw_data": _float_to_decimal(record.raw_data),
            "source": record.source,
            "collected_at": record.collected_at,
            "ttl": _ttl(TTL_SIGNALS_DAYS),
        }
    )


def get_latest_signals(region_code: str) -> dict[str, int]:
    """Get the most recent score for each signal class for a region.

    Issues a single DynamoDB Query with begins_with("SIG#") to fetch all
    signal classes in one API round-trip, reducing 7 × N queries to 1 × N.
    Items are returned in descending SK order (SIG#G#... → SIG#A#...) so the
    first item seen for each class letter is the most recent.

    Returns: {"A": 8, "B": 3, ...} — missing classes default to 0.
    """
    table = _dynamodb.Table(SIGNALS_TABLE)
    result: dict[str, int] = {cls.value: 0 for cls in SignalClass}
    seen: set[str] = set()
    all_classes = {cls.value for cls in SignalClass}

    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": (
            Key("PK").eq(f"REGION#{region_code}")
            & Key("SK").begins_with("SIG#")
        ),
        "ScanIndexForward": False,
        "ProjectionExpression": "signal_class, score",
    }

    # Paginate until we have found the latest score for every class.
    while True:
        resp = table.query(**query_kwargs)
        for item in resp.get("Items", []):
            cls_val = str(item.get("signal_class", ""))
            if cls_val and cls_val not in seen:
                seen.add(cls_val)
                result[cls_val] = int(item["score"])
            if seen >= all_classes:
                return result

        if "LastEvaluatedKey" not in resp:
            break
        query_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    return result


def get_signal_history(
    region_code: str, signal_class: str, limit: int = 48
) -> list[dict[str, Any]]:
    """Get recent signal records for a region + class (newest first).

    Args:
        region_code: AWS Region code, e.g. "ap-northeast-1".
        signal_class: Single letter, e.g. "B".
        limit: Maximum number of records to return (default 48 ≈ 8 h at 10-min cadence).

    Returns:
        List of DynamoDB item dicts with at least ``score`` and ``raw_data``.
    """
    table = _dynamodb.Table(SIGNALS_TABLE)
    resp = table.query(
        KeyConditionExpression=(
            Key("PK").eq(f"REGION#{region_code}")
            & Key("SK").begins_with(f"SIG#{signal_class}#")
        ),
        ScanIndexForward=False,
        Limit=limit,
        ProjectionExpression="score, raw_data",
    )
    return resp.get("Items", [])


# ── GPRI ──


def put_gpri(record: GpriRecord) -> None:
    """Write a GPRI score snapshot to DynamoDB."""
    table = _dynamodb.Table(GPRI_TABLE)
    table.put_item(
        Item={
            "PK": record.pk,
            "SK": record.sk,
            "gpri": record.gpri,
            "level": record.level.value,
            "prev_level": record.prev_level.value if record.prev_level else None,
            "components": record.components,
            "baseline": record.baseline,
            "compliance_block": record.compliance_block,
            "ttl": _ttl(TTL_GPRI_DAYS),
        }
    )


def get_previous_level(region_code: str) -> GpriLevel | None:
    """Get the most recent GPRI level for a region, or None if no history."""
    table = _dynamodb.Table(GPRI_TABLE)
    resp = table.query(
        KeyConditionExpression=(
            Key("PK").eq(f"REGION#{region_code}")
            & Key("SK").begins_with("TS#")
        ),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        return None
    return GpriLevel(items[0]["level"])


def get_gpri_history(
    region_code: str, limit: int = 288
) -> list[dict[str, Any]]:
    """Get recent GPRI history for a region (default: 24h at 5min intervals)."""
    table = _dynamodb.Table(GPRI_TABLE)
    resp = table.query(
        KeyConditionExpression=(
            Key("PK").eq(f"REGION#{region_code}")
            & Key("SK").begins_with("TS#")
        ),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])
