"""Unit tests for C-class political / travel-advisory collector."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from collectors.political import (
    _iso2_to_level,
    _parse_rss_levels,
    collect_political_signals,
)


# ── _parse_rss_levels ──────────────────────────────────────────────────────────

class TestParseRssLevels:
    """Verify State Dept RSS title parsing."""

    def test_parses_level_from_title(self) -> None:
        xml = "<title><![CDATA[Japan - Level 1: Normal Precautions]]></title>"
        levels = _parse_rss_levels(xml)
        assert levels.get("Japan") == 1

    def test_level_4_is_captured(self) -> None:
        xml = "<title><![CDATA[Israel - Level 4: Do Not Travel]]></title>"
        levels = _parse_rss_levels(xml)
        assert levels.get("Israel") == 4

    def test_multiple_countries(self) -> None:
        xml = (
            "<title><![CDATA[Japan - Level 1: Normal]]></title>"
            "<title><![CDATA[Mexico - Level 3: Reconsider]]></title>"
        )
        levels = _parse_rss_levels(xml)
        assert levels["Japan"] == 1
        assert levels["Mexico"] == 3

    def test_invalid_level_ignored(self) -> None:
        xml = "<title><![CDATA[Mars - Level 9: Unknown]]></title>"
        levels = _parse_rss_levels(xml)
        assert "Mars" not in levels  # level 9 is outside 1-4 range


# ── _iso2_to_level ─────────────────────────────────────────────────────────────

class TestIso2ToLevel:
    """Verify country code → advisory level resolution."""

    def test_rss_match_returns_rss_level(self) -> None:
        rss_levels = {"Japan": 1, "Israel": 4}
        level, source = _iso2_to_level("JP", rss_levels)
        assert level == 1
        assert source == "state_dept_rss"

    def test_static_fallback_used_when_no_rss(self) -> None:
        level, source = _iso2_to_level("JP", {})
        assert level == 1
        assert source == "static_fallback"

    def test_unknown_country_defaults_to_1(self) -> None:
        level, _ = _iso2_to_level("XX", {})
        assert level == 1


# ── collect_political_signals ──────────────────────────────────────────────────

class TestCollectPoliticalSignals:
    """Integration tests with mocked HTTP."""

    def test_returns_records_for_all_regions(self) -> None:
        from shared.region_config import ALL_REGIONS

        xml = "<title><![CDATA[Japan - Level 1: Normal]]></title>"
        with (
            patch("collectors.political.get_text", return_value=xml),
            patch("collectors.political.put_signal"),
        ):
            records = collect_political_signals()

        assert len(records) == len(ALL_REGIONS)
        for r in records:
            assert r.signal_class.value == "C"
            assert 0 <= r.score <= 15

    def test_rss_failure_falls_back_to_static(self) -> None:
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.political.get_text", side_effect=RuntimeError("network error")),
            patch("collectors.political.put_signal"),
        ):
            records = collect_political_signals()

        assert len(records) == len(ALL_REGIONS)
        # Static scores should not be 0 for high-risk countries like IL
        il_record = next(r for r in records if r.region == "il-central-1")
        assert il_record.score > 0

    def test_level_4_country_scores_15(self) -> None:
        xml = "<title><![CDATA[Israel - Level 4: Do Not Travel]]></title>"
        with (
            patch("collectors.political.get_text", return_value=xml),
            patch("collectors.political.put_signal"),
        ):
            records = collect_political_signals()

        il_record = next(r for r in records if r.region == "il-central-1")
        assert il_record.score == 15
