"""HTTP client with retry and timeout for external API calls."""
from __future__ import annotations

import time
import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30  # seconds
_DEFAULT_RETRIES = 3
_BACKOFF_FACTOR = 0.5


def _build_session() -> requests.Session:
    """Build a requests.Session with retry strategy."""
    session = requests.Session()
    retry = Retry(
        total=_DEFAULT_RETRIES,
        backoff_factor=_BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "DR-Geopolitical-Alert/0.1 (AWS Lambda)",
        "Accept": "application/json",
    })
    return session


_session = _build_session()


def get_json(url: str, params: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None,
             timeout: int = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    """GET request returning parsed JSON, with retry and timeout."""
    start = time.monotonic()
    try:
        resp = _session.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        elapsed = time.monotonic() - start
        logger.info("GET %s → %d (%.1fs)", url, resp.status_code, elapsed)
        return resp.json()
    except requests.RequestException as exc:
        elapsed = time.monotonic() - start
        logger.warning("GET %s failed after %.1fs: %s", url, elapsed, exc)
        raise


def get_text(url: str, params: dict[str, Any] | None = None,
             timeout: int = _DEFAULT_TIMEOUT) -> str:
    """GET request returning raw text (for RSS/XML feeds)."""
    try:
        resp = _session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        logger.warning("GET %s failed: %s", url, exc)
        raise
