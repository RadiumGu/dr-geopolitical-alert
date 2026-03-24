"""GPRI Calculator — aggregates signal scores into a 0-100 risk index per Region.

Runs every 5 minutes via EventBridge Scheduler.
For each Region:
  1. Read latest signal scores (A-G) from DynamoDB
  2. Add baseline + signal scores → GPRI (capped at 100)
  3. Determine level (GREEN/YELLOW/ORANGE/RED/BLACK)
  4. If level changed → publish SNS alert
  5. Emit CloudWatch custom metrics
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

from shared.types import SignalClass, GpriLevel, GpriRecord, gpri_to_level
from shared.region_config import ALL_REGIONS, REGION_MAP
from shared.db import get_latest_signals, put_gpri, get_previous_level

logger = logging.getLogger(__name__)

_sns = boto3.client("sns")
_cw = boto3.client("cloudwatch")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
CW_NAMESPACE = "DrAlert/GPRI"

LEVEL_EMOJI = {
    GpriLevel.GREEN: "🟢",
    GpriLevel.YELLOW: "🟡",
    GpriLevel.ORANGE: "🟠",
    GpriLevel.RED: "🔴",
    GpriLevel.BLACK: "⚫",
}

LEVEL_ACTIONS = {
    GpriLevel.GREEN: "正常运营",
    GpriLevel.YELLOW: "加强监控，Review DR 就绪状态",
    GpriLevel.ORANGE: "主动备战：Scale Up 备用 Region，降低 TTL",
    GpriLevel.RED: "建议撤离：启动切换决策流程",
    GpriLevel.BLACK: "立即执行 DR 切换",
}


def _calc_gpri(region_code: str, baseline: int) -> GpriRecord:
    """Calculate GPRI for a single Region."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signals = get_latest_signals(region_code)

    # Sum baseline + all signal scores
    total = baseline
    for cls in SignalClass:
        total += signals.get(cls.value, 0)
    total = min(total, 100)

    level = gpri_to_level(total)
    prev_level = get_previous_level(region_code)

    # Check compliance block (F-class score >= 8)
    compliance_block = signals.get("F", 0) >= 8

    record = GpriRecord(
        region=region_code,
        gpri=total,
        level=level,
        prev_level=prev_level,
        components=signals,
        baseline=baseline,
        compliance_block=compliance_block,
        timestamp=now,
    )
    return record


def _publish_level_change(record: GpriRecord) -> None:
    """Publish SNS notification when GPRI level changes."""
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not set, skipping notification")
        return

    region_cfg = REGION_MAP.get(record.region)
    city = region_cfg.city if region_cfg else record.region
    emoji = LEVEL_EMOJI.get(record.level, "❓")
    action = LEVEL_ACTIONS.get(record.level, "")

    # Format components breakdown
    comp_lines = []
    for cls in SignalClass:
        val = record.components.get(cls.value, 0)
        from shared.types import MAX_SCORES
        mx = MAX_SCORES[cls]
        bar = "█" * val + "░" * (mx - val)
        comp_lines.append(f"  {cls.value} {bar} {val}/{mx}")
    components_text = "\n".join(comp_lines)

    prev_str = record.prev_level.value if record.prev_level else "N/A"
    direction = "↑" if record.prev_level and record.level.value > record.prev_level.value else "↓"

    message = {
        "region": record.region,
        "city": city,
        "gpri": record.gpri,
        "level": record.level.value,
        "prev_level": prev_str,
        "components": record.components,
        "baseline": record.baseline,
        "compliance_block": record.compliance_block,
        "action": action,
        "timestamp": record.timestamp,
    }

    subject = f"{emoji} GPRI {record.level.value} — {record.region} ({city}) [{record.gpri}/100]"

    body = f"""{emoji} GPRI {record.level.value} — {record.region} ({city})
Score: {record.gpri}/100 ({direction} from {prev_str})

Components:
{components_text}

Baseline: {record.baseline}
Compliance Block: {"🚫 YES" if record.compliance_block else "✅ No"}

建议: {action}
时间: {record.timestamp}"""

    _sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=subject[:100],
        Message=body,
        MessageAttributes={
            "level": {"DataType": "String", "StringValue": record.level.value},
            "region": {"DataType": "String", "StringValue": record.region},
        },
    )
    logger.info("Published SNS alert: %s", subject)


def _emit_metrics(record: GpriRecord) -> None:
    """Emit CloudWatch custom metrics for GPRI."""
    level_num = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3, "BLACK": 4}

    metric_data = [
        {
            "MetricName": "Score",
            "Dimensions": [{"Name": "Region", "Value": record.region}],
            "Value": record.gpri,
            "Unit": "None",
        },
        {
            "MetricName": "Level",
            "Dimensions": [{"Name": "Region", "Value": record.region}],
            "Value": level_num.get(record.level.value, 0),
            "Unit": "None",
        },
    ]

    # Per-dimension scores
    for cls in SignalClass:
        val = record.components.get(cls.value, 0)
        metric_data.append({
            "MetricName": "SignalScore",
            "Dimensions": [
                {"Name": "Region", "Value": record.region},
                {"Name": "Class", "Value": cls.value},
            ],
            "Value": val,
            "Unit": "None",
        })

    _cw.put_metric_data(Namespace=CW_NAMESPACE, MetricData=metric_data)


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point — recalculate GPRI for all Regions."""
    results = []
    alerts_sent = 0

    for region in ALL_REGIONS:
        try:
            record = _calc_gpri(region.code, region.baseline)
            put_gpri(record)
            _emit_metrics(record)

            # Check for level change
            if record.prev_level is not None and record.level != record.prev_level:
                _publish_level_change(record)
                alerts_sent += 1
                logger.info(
                    "LEVEL CHANGE: %s %s → %s (GPRI=%d)",
                    region.code, record.prev_level.value, record.level.value, record.gpri,
                )

            results.append({
                "region": region.code,
                "gpri": record.gpri,
                "level": record.level.value,
            })

        except Exception as exc:
            logger.error("Failed to calculate GPRI for %s: %s", region.code, exc)

    # Log summary
    high_risk = [r for r in results if r["level"] in ("ORANGE", "RED", "BLACK")]
    logger.info(
        "GPRI calculated for %d regions, %d alerts sent, %d high-risk",
        len(results), alerts_sent, len(high_risk),
    )

    return {
        "statusCode": 200,
        "body": {
            "regions_calculated": len(results),
            "alerts_sent": alerts_sent,
            "high_risk": high_risk,
        },
    }
