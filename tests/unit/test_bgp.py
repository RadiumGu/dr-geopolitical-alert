"""Unit tests for G-class BGP collector."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from collectors.bgp import (
    _score_cf_hijacks,
    _score_ioda,
    collect_bgp_signals,
)


# ── _score_ioda ────────────────────────────────────────────────────────────────

def _ioda_response(drop_pct: float) -> dict:
    """Build a minimal IODA response with the given drop percentage."""
    # baseline=100, current = 100 * (1 - drop_pct/100)
    baseline = 100.0
    current = baseline * (1 - drop_pct / 100)
    return {
        "data": [
            [
                {
                    "datasource": "bgp",
                    "values": [
                        baseline,
                        baseline,
                        baseline,
                        baseline,
                        baseline,
                        current,
                    ],
                }
            ]
        ]
    }


class TestScoreIoda:
    """Verify IODA drop-percentage → score mapping."""

    def test_no_data_returns_zero(self) -> None:
        score, _ = _score_ioda({})
        assert score == 0

    def test_no_drop_returns_zero(self) -> None:
        score, _ = _score_ioda(_ioda_response(0))
        assert score == 0

    def test_small_drop_below_5pct_returns_zero(self) -> None:
        score, _ = _score_ioda(_ioda_response(3))
        assert score == 0

    def test_minor_drop_5_to_20pct(self) -> None:
        score, _ = _score_ioda(_ioda_response(10))
        assert 1 <= score <= 4

    def test_moderate_drop_20_to_50pct(self) -> None:
        score, _ = _score_ioda(_ioda_response(35))
        assert 5 <= score <= 9

    def test_severe_drop_over_50pct(self) -> None:
        score, _ = _score_ioda(_ioda_response(60))
        assert 10 <= score <= 15

    def test_score_capped_at_15(self) -> None:
        score, _ = _score_ioda(_ioda_response(100))
        assert score <= 15


# ── _score_cf_hijacks ─────────────────────────────────────────────────────────

class TestScoreCfHijacks:
    """Verify Cloudflare BGP hijack scoring."""

    def test_no_hijacks_returns_zero(self) -> None:
        assert _score_cf_hijacks([]) == 0

    def test_one_low_conf_hijack(self) -> None:
        assert _score_cf_hijacks([{"id": 1, "confidence_score": 2}]) == 4

    def test_one_high_conf_hijack(self) -> None:
        assert _score_cf_hijacks([{"id": 1, "confidence_score": 12}]) == 4

    def test_many_high_conf_capped(self) -> None:
        hijacks = [{"id": i, "confidence_score": 10} for i in range(10)]
        assert _score_cf_hijacks(hijacks) == 8

    def test_many_low_conf_capped_at_7(self) -> None:
        hijacks = [{"id": i, "confidence_score": 2} for i in range(20)]
        assert _score_cf_hijacks(hijacks) == 7


# ── collect_bgp_signals ────────────────────────────────────────────────────────

class TestCollectBgpSignals:
    """Integration tests with mocked HTTP."""

    def test_returns_records_for_all_regions(self) -> None:
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.bgp._fetch_ioda_signals", return_value={}),
            patch("collectors.bgp.get_secret", return_value=""),
            patch("collectors.bgp.put_signal"),
        ):
            records = collect_bgp_signals()

        assert len(records) == len(ALL_REGIONS)
        for r in records:
            assert r.signal_class.value == "G"
            assert r.score == 0

    def test_ioda_failure_returns_zero_score(self) -> None:
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.bgp._fetch_ioda_signals", side_effect=RuntimeError("timeout")),
            patch("collectors.bgp.get_secret", return_value=""),
            patch("collectors.bgp.put_signal"),
        ):
            records = collect_bgp_signals()

        assert len(records) == len(ALL_REGIONS)
        for r in records:
            assert r.score == 0

    def test_high_drop_ioda_gives_non_zero_score(self) -> None:
        with (
            patch("collectors.bgp._fetch_ioda_signals", return_value=_ioda_response(60)),
            patch("collectors.bgp.get_secret", return_value=""),
            patch("collectors.bgp.put_signal"),
        ):
            records = collect_bgp_signals()

        for r in records:
            assert r.score >= 10
