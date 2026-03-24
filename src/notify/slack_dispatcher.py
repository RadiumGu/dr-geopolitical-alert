"""Slack notification dispatcher for GPRI level change alerts.

Lambda handler triggered by SNS topic.
Parses the plain-text SNS message body produced by gpri_calculator._publish_level_change
and POSTs a Block Kit message to a Slack Incoming Webhook.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from typing import Any

import boto3

logger = logging.getLogger(__name__)

_ssm = boto3.client("ssm")


def _get_webhook_url() -> str:
    """Retrieve the Slack webhook URL from SSM Parameter Store at runtime.

    Reads SLACK_WEBHOOK_SSM_PATH from the environment and fetches the
    SecureString parameter value.  Returns empty string on any error.
    """
    path = os.environ.get("SLACK_WEBHOOK_SSM_PATH", "")
    if not path:
        logger.warning("SLACK_WEBHOOK_SSM_PATH not set")
        return ""
    try:
        resp = _ssm.get_parameter(Name=path, WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception as exc:
        logger.error("Failed to fetch SSM parameter %s: %s", path, exc)
        return ""

LEVEL_EMOJI: dict[str, str] = {
    "GREEN": "🟢",
    "YELLOW": "🟡",
    "ORANGE": "🟠",
    "RED": "🔴",
    "BLACK": "⚫",
}

LEVEL_COLOR: dict[str, str] = {
    "GREEN": "#2eb886",
    "YELLOW": "#f2c744",
    "ORANGE": "#f26522",
    "RED": "#d9241a",
    "BLACK": "#1a1a1a",
}


def _parse_sns_body(body: str) -> dict[str, str]:
    """Parse the plain-text SNS message body into a structured dict.

    Expected format (produced by gpri_calculator._publish_level_change):
        {emoji} GPRI {LEVEL} — {region} ({city})
        Score: {n}/100 ({direction} from {prev_level})

        Components:
          A ████░░░░░░░░░░░░░░░░ 4/20
          ...

        Baseline: {n}
        Compliance Block: {text}

        建议: {action}
        时间: {timestamp}

    Args:
        body: Raw SNS Message string (plain text).

    Returns:
        Dict with keys: emoji, level, region, city, score, direction,
        prev_level, components_text, baseline, compliance_block, action, timestamp.
    """
    lines = body.strip().splitlines()
    result: dict[str, str] = {}

    # Line 0: "{emoji} GPRI {LEVEL} — {region} ({city})"
    if lines:
        m = re.match(r"(.+?)\s+GPRI\s+(\w+)\s+—\s+(\S+)\s+\((.+?)\)", lines[0])
        if m:
            result["emoji"] = m.group(1).strip()
            result["level"] = m.group(2)
            result["region"] = m.group(3)
            result["city"] = m.group(4)

    # "Score: {n}/100 ({direction} from {prev_level})"
    for line in lines:
        m = re.match(r"Score:\s+(\d+)/100\s+\((.+?) from (.+?)\)", line)
        if m:
            result["score"] = m.group(1)
            result["direction"] = m.group(2)
            result["prev_level"] = m.group(3)
            break

    # Components section (lines between "Components:" and the next blank line)
    comp_lines: list[str] = []
    in_components = False
    for line in lines:
        if line.strip() == "Components:":
            in_components = True
            continue
        if in_components:
            if line.strip() == "":
                break
            comp_lines.append(line.strip())
    result["components_text"] = "\n".join(comp_lines)

    # "Baseline: {n}"
    for line in lines:
        m = re.match(r"Baseline:\s+(\d+)", line)
        if m:
            result["baseline"] = m.group(1)
            break

    # "Compliance Block: {text}"
    for line in lines:
        if line.startswith("Compliance Block:"):
            result["compliance_block"] = line.split(":", 1)[1].strip()
            break

    # "建议: {action}"
    for line in lines:
        if line.startswith("建议:"):
            result["action"] = line.split(":", 1)[1].strip()
            break

    # "时间: {timestamp}"
    for line in lines:
        if line.startswith("时间:"):
            result["timestamp"] = line.split(":", 1)[1].strip()
            break

    return result


def _build_blocks(parsed: dict[str, str]) -> list[dict[str, Any]]:
    """Build Slack Block Kit blocks from a parsed SNS message dict.

    Args:
        parsed: Dict produced by _parse_sns_body.

    Returns:
        List of Slack Block Kit block dicts.
    """
    level = parsed.get("level", "UNKNOWN")
    emoji = parsed.get("emoji", LEVEL_EMOJI.get(level, "❓"))
    region = parsed.get("region", "unknown")
    city = parsed.get("city", "")
    score = parsed.get("score", "?")
    direction = parsed.get("direction", "")
    prev_level = parsed.get("prev_level", "N/A")
    components_text = parsed.get("components_text", "")
    action = parsed.get("action", "")
    timestamp = parsed.get("timestamp", "")
    compliance = parsed.get("compliance_block", "")

    blocks: list[dict[str, Any]] = [
        # Header: emoji + level + region + city
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} GPRI {level} — {region} ({city})",
                "emoji": True,
            },
        },
        # Section: score / direction + compliance block
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Score*\n`{score}/100`  {direction} from *{prev_level}*",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Compliance Block*\n{compliance}",
                },
            ],
        },
    ]

    # Section: per-dimension bar chart
    if components_text:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Components*\n```{components_text}```",
            },
        })

    # Section: recommended action
    if action:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":bulb: *建议动作*\n{action}",
            },
        })

    # Context: timestamp
    if timestamp:
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"时间: {timestamp}",
                }
            ],
        })

    return blocks


def _post_to_slack(payload: dict[str, Any]) -> None:
    """POST a JSON payload to the Slack Incoming Webhook.

    Fetches the webhook URL from SSM Parameter Store at invocation time so
    the URL is never stored in environment variables or CloudFormation.

    Args:
        payload: Slack message payload dict.

    Raises:
        RuntimeError: If the webhook returns a non-200 status code.
    """
    webhook_url = _get_webhook_url()
    if not webhook_url:
        logger.warning("Slack webhook URL unavailable, skipping notification")
        return

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        status = resp.getcode()
        if status != 200:
            raise RuntimeError(f"Slack webhook returned HTTP {status}")
    logger.info("Slack notification sent (HTTP 200)")


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point — dispatch SNS GPRI alerts to Slack.

    Args:
        event: Lambda event containing SNS Records.
        context: Lambda context (unused).

    Returns:
        Dict with dispatched/errors counts.
    """
    records = event.get("Records", [])
    dispatched = 0
    errors = 0

    for record in records:
        try:
            sns_record = record.get("Sns", {})
            body = sns_record.get("Message", "")
            subject = sns_record.get("Subject", "")

            if not body:
                logger.warning("Empty SNS message body, skipping record")
                continue

            parsed = _parse_sns_body(body)
            blocks = _build_blocks(parsed)

            level = parsed.get("level", "UNKNOWN")
            color = LEVEL_COLOR.get(level, "#888888")

            payload: dict[str, Any] = {
                "text": subject or f"GPRI Alert: {level}",
                "attachments": [
                    {
                        "color": color,
                        "blocks": blocks,
                    }
                ],
            }

            _post_to_slack(payload)
            dispatched += 1

        except Exception as exc:
            logger.error("Failed to dispatch SNS record to Slack: %s", exc)
            errors += 1

    return {
        "statusCode": 200,
        "body": {
            "dispatched": dispatched,
            "errors": errors,
        },
    }
