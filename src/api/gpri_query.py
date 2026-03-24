"""Lambda Function URL handler — query GPRI scores."""
from __future__ import annotations

import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

GPRI_TABLE = os.environ["GPRI_TABLE"]
_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-west-2"))


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == int(o) else float(o)
        return super().default(o)


def _get_gpri(region_code: str) -> dict | None:
    table = _dynamodb.Table(GPRI_TABLE)
    resp = table.query(
        KeyConditionExpression=Key("PK").eq(f"REGION#{region_code}"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        return None
    item = items[0]
    components = item.get("components", {})
    return {
        "region": region_code,
        "gpri": item.get("gpri", 0),
        "level": item.get("level", "UNKNOWN"),
        "confidence": item.get("confidence", "UNKNOWN"),
        "components": {k: v for k, v in sorted(components.items())},
        "timestamp": item.get("SK", "").replace("GPRI#", ""),
    }


def _get_all_regions() -> list[dict]:
    """Return latest GPRI for all 34 regions."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from shared.region_config import ALL_REGIONS

    results = []
    for r in sorted(ALL_REGIONS, key=lambda x: -x.baseline):
        data = _get_gpri(r.code)
        if data:
            data["city"] = r.city
            data["country"] = r.country
            data["baseline"] = r.baseline
            results.append(data)
    return results


def handler(event, context):
    """Handle Function URL requests.

    GET /?region=il-central-1  → single region
    GET /                      → all 34 regions
    """
    params = event.get("queryStringParameters") or {}
    region = params.get("region", "").strip()

    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
    }

    if region:
        data = _get_gpri(region)
        if not data:
            return {
                "statusCode": 404,
                "headers": headers,
                "body": json.dumps({"error": f"Region '{region}' not found"}),
            }
        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps(data, cls=DecimalEncoder),
        }
    else:
        results = _get_all_regions()
        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"count": len(results), "regions": results}, cls=DecimalEncoder),
        }
