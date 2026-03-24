"""DynamoDB operations for signals and GPRI tables."""
from __future__ import annotations

import time
from datetime import datetime, timezone
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
            "raw_data": record.raw_data,
            "source": record.source,
            "collected_at": record.collected_at,
            "ttl": _ttl(TTL_SIGNALS_DAYS),
        }
    )


def get_latest_signals(region_code: str) -> dict[str, int]:
    """Get the most recent score for each signal class for a region.

    Returns: {"A": 8, "B": 3, ...} — missing classes default to 0.
    """
    table = _dynamodb.Table(SIGNALS_TABLE)
    result: dict[str, int] = {}

    for cls in SignalClass:
        resp = table.query(
            KeyConditionExpression=(
                Key("PK").eq(f"REGION#{region_code}")
                & Key("SK").begins_with(f"SIG#{cls.value}#")
            ),
            ScanIndexForward=False,  # newest first
            Limit=1,
        )
        items = resp.get("Items", [])
        result[cls.value] = int(items[0]["score"]) if items else 0

    return result


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
