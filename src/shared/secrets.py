"""Runtime secret loader — reads tokens from SSM Parameter Store at Lambda cold start.

Usage in collectors:
    from shared.secrets import get_secret
    token = get_secret("/dr-alert/ucdp-access-token")  # Returns "" if not found

Tokens are cached for the lifetime of the Lambda container (~15 min to hours).
No CDK wiring needed — just create SSM parameters and they're picked up automatically.
"""
from __future__ import annotations

import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}
_ssm_client = None


def _get_ssm_client():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-west-2"))
    return _ssm_client


def get_secret(param_name: str, default: str = "") -> str:
    """Get a secret from SSM Parameter Store (cached per container).

    Returns the parameter value, or *default* if the parameter doesn't exist.
    """
    if param_name in _cache:
        return _cache[param_name]

    # Also check env var override (useful for local testing)
    env_key = param_name.strip("/").replace("/", "_").replace("-", "_").upper()
    env_val = os.environ.get(env_key, "")
    if env_val and env_val != "PENDING":
        _cache[param_name] = env_val
        return env_val

    try:
        resp = _get_ssm_client().get_parameter(Name=param_name, WithDecryption=True)
        value = resp["Parameter"]["Value"]
        if value == "PENDING":
            value = ""
        _cache[param_name] = value
        return value
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            logger.debug("SSM parameter %s not found, using default", param_name)
            _cache[param_name] = default
            return default
        raise
