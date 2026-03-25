"""Weekly dynamic baseline calibrator.

Runs every Sunday 00:00 UTC via EventBridge Scheduler.
For each Region:
  1. Query 30 days of signal history (A-G)
  2. Compute median score per dimension, sum → signal_median_sum
  3. deviation = signal_median_sum - static_baseline
  4. delta = clamp(round(deviation * DAMPING), -MAX_DELTA, +MAX_DELTA)
  5. Store delta in DynamoDB (CONFIG#baseline_delta)
  6. If delta changed → collect for SNS summary

Formula:
  effective_baseline = static_baseline + delta
  GPRI = effective_baseline + Σ(signals)
"""
from __future__ import annotations

import logging
import os
import statistics
from typing import Any

import boto3

from shared.region_config import ALL_REGIONS
from shared.db import (
    get_signal_scores_for_calibration,
    get_all_baseline_deltas,
    put_baseline_delta,
)
from shared.types import SignalClass

logger = logging.getLogger(__name__)

_sns = boto3.client("sns")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")

# Calibration parameters
DAMPING = 0.3          # Smoothing coefficient — prevents overreaction
MAX_DELTA = 5          # Maximum absolute delta (±5)
HISTORY_DAYS = 30      # Lookback window for signal history
MIN_SAMPLES = 50       # Minimum signal samples required per dimension


def _median(values: list[int]) -> float:
    """Return median of a list, or 0.0 if empty."""
    if not values:
        return 0.0
    return statistics.median(values)


def calibrate_region(
    region_code: str,
    static_baseline: int,
    current_delta: int,
) -> dict:
    """Calibrate baseline for a single region.

    Returns:
        dict with keys: region, static_baseline, signal_median_sum,
        deviation, new_delta, old_delta, changed, reason
    """
    scores = get_signal_scores_for_calibration(region_code, days=HISTORY_DAYS)

    # Compute median per dimension
    medians: dict[str, float] = {}
    total_samples = 0
    for cls in SignalClass:
        vals = scores.get(cls.value, [])
        total_samples += len(vals)
        medians[cls.value] = _median(vals)

    signal_median_sum = sum(medians.values())

    # Check if we have enough data
    if total_samples < MIN_SAMPLES:
        return {
            "region": region_code,
            "static_baseline": static_baseline,
            "signal_median_sum": round(signal_median_sum, 2),
            "deviation": 0,
            "new_delta": current_delta,  # Keep existing
            "old_delta": current_delta,
            "changed": False,
            "reason": f"insufficient_data ({total_samples} samples < {MIN_SAMPLES})",
            "medians": {k: round(v, 2) for k, v in medians.items()},
            "total_samples": total_samples,
        }

    deviation = signal_median_sum - static_baseline
    raw_delta = deviation * DAMPING
    new_delta = max(-MAX_DELTA, min(MAX_DELTA, round(raw_delta)))

    changed = new_delta != current_delta

    if changed:
        reason = (
            f"median_sum={signal_median_sum:.1f}, "
            f"deviation={deviation:+.1f}, "
            f"damped={raw_delta:+.1f} → delta={new_delta:+d}"
        )
    else:
        reason = "no_change"

    return {
        "region": region_code,
        "static_baseline": static_baseline,
        "signal_median_sum": round(signal_median_sum, 2),
        "deviation": round(deviation, 2),
        "new_delta": new_delta,
        "old_delta": current_delta,
        "changed": changed,
        "reason": reason,
        "medians": {k: round(v, 2) for k, v in medians.items()},
        "total_samples": total_samples,
    }


def _publish_summary(changes: list[dict]) -> None:
    """Publish SNS summary of baseline delta changes."""
    if not SNS_TOPIC_ARN or not changes:
        return

    lines = ["📊 Weekly Baseline Calibration — Delta Changes\n"]
    for c in changes:
        arrow = "↑" if c["new_delta"] > c["old_delta"] else "↓"
        lines.append(
            f"  {c['region']}: delta {c['old_delta']:+d} → {c['new_delta']:+d} {arrow}  "
            f"(effective: {c['static_baseline'] + c['new_delta']})\n"
            f"    median_sum={c['signal_median_sum']}, "
            f"static={c['static_baseline']}"
        )

    body = "\n".join(lines)
    subject = f"📊 Baseline Calibration: {len(changes)} region(s) adjusted"

    try:
        _sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject[:100],
            Message=body,
            MessageAttributes={
                "event_type": {
                    "DataType": "String",
                    "StringValue": "baseline_calibration",
                },
            },
        )
        logger.info("Published calibration summary: %d changes", len(changes))
    except Exception as e:
        logger.error("Failed to publish calibration summary: %s", e)


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point — weekly baseline calibration for all regions."""
    current_deltas = get_all_baseline_deltas()
    changes: list[dict] = []
    results: list[dict] = []
    errors = 0

    for region in ALL_REGIONS:
        try:
            current_delta = current_deltas.get(region.code, 0)
            result = calibrate_region(region.code, region.baseline, current_delta)
            results.append(result)

            if result["changed"]:
                put_baseline_delta(
                    region_code=region.code,
                    delta=result["new_delta"],
                    static_baseline=region.baseline,
                    signal_median_sum=result["signal_median_sum"],
                    reason=result["reason"],
                )
                changes.append(result)
                logger.info(
                    "BASELINE DELTA CHANGE: %s delta %+d → %+d (effective: %d)",
                    region.code,
                    result["old_delta"],
                    result["new_delta"],
                    region.baseline + result["new_delta"],
                )
        except Exception as e:
            logger.error("Failed to calibrate %s: %s", region.code, e)
            errors += 1

    # Publish SNS summary if any deltas changed
    if changes:
        _publish_summary(changes)

    logger.info(
        "Calibration complete: %d regions processed, %d changed, %d errors",
        len(results), len(changes), errors,
    )

    return {
        "statusCode": 200,
        "body": {
            "regions_processed": len(results),
            "deltas_changed": len(changes),
            "errors": errors,
            "changes": [
                {
                    "region": c["region"],
                    "old_delta": c["old_delta"],
                    "new_delta": c["new_delta"],
                    "effective_baseline": c["static_baseline"] + c["new_delta"],
                }
                for c in changes
            ],
        },
    }
