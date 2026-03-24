"""Unit tests for B-class cyber collector."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from collectors.cyber import (
    _absolute_score,
    _count_threats_by_country,
    collect_cyber_signals,
)


# ── _count_threats_by_country ──────────────────────────────────────────────────

class TestCountThreatsByCountry:
    """Verify aggregation of Feodo + URLhaus country codes."""

    def test_feodo_country_counted(self) -> None:
        feodo = [{"country": "jp"}, {"country": "JP"}, {"country": "US"}]
        counts = _count_threats_by_country(feodo, [])
        assert counts["JP"] == 2
        assert counts["US"] == 1

    def test_urlhaus_country_code_counted(self) -> None:
        urlhaus = [{"country_code": "DE"}, {"country_code": "DE"}]
        counts = _count_threats_by_country([], urlhaus)
        assert counts["DE"] == 2

    def test_missing_country_skipped(self) -> None:
        feodo = [{"country": ""}, {}]
        counts = _count_threats_by_country(feodo, [])
        assert counts == {}


# ── _absolute_score (fallback) ─────────────────────────────────────────────────

class TestAbsoluteScore:
    """Verify absolute score thresholds used as fallback."""

    def test_zero_threats(self) -> None:
        assert _absolute_score(0) == 0

    def test_one_threat(self) -> None:
        assert _absolute_score(1) == 1

    def test_ten_threats(self) -> None:
        assert _absolute_score(10) == 6

    def test_fifty_threats(self) -> None:
        assert _absolute_score(50) == 12

    def test_capped_at_max(self) -> None:
        assert _absolute_score(10000) == 15


# ── collect_cyber_signals ──────────────────────────────────────────────────────

class TestCollectCyberSignals:
    """Integration tests with mocked HTTP and DB."""

    def test_returns_records_for_all_regions(self) -> None:
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.cyber._fetch_feodo", return_value=[]),
            patch("collectors.cyber._fetch_urlhaus", return_value=[]),
            patch("collectors.cyber.get_signal_history", return_value=[]),
            patch("collectors.cyber.put_signal"),
        ):
            records = collect_cyber_signals()

        assert len(records) == len(ALL_REGIONS)
        for r in records:
            assert r.signal_class.value == "B"
            assert r.score == 0

    def test_fallback_to_absolute_score_when_no_history(self) -> None:
        """When no historical data, score uses absolute count."""
        feodo_data = [{"country": "JP"}] * 55  # > 50 → absolute score 12+

        with (
            patch("collectors.cyber._fetch_feodo", return_value=feodo_data),
            patch("collectors.cyber._fetch_urlhaus", return_value=[]),
            patch("collectors.cyber.get_signal_history", return_value=[]),
            patch("collectors.cyber.put_signal"),
        ):
            records = collect_cyber_signals()

        jp_records = [r for r in records if r.region in ("ap-northeast-1", "ap-northeast-3")]
        for r in jp_records:
            assert r.score >= 12

    def test_feodo_fetch_error_gives_zero_score(self) -> None:
        with (
            patch("collectors.cyber._fetch_feodo", side_effect=RuntimeError("down")),
            patch("collectors.cyber._fetch_urlhaus", return_value=[]),
            patch("collectors.cyber.get_signal_history", return_value=[]),
            patch("collectors.cyber.put_signal"),
        ):
            records = collect_cyber_signals()

        for r in records:
            assert r.score == 0
