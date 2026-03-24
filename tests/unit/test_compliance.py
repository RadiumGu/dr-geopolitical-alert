"""Unit tests for F-class compliance / sanctions collector."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from collectors.compliance import (
    _count_hits,
    _parse_rss_items,
    _sanctions_score,
    collect_compliance_signals,
)


# ── _parse_rss_items ───────────────────────────────────────────────────────────

class TestParseRssItems:
    """Verify RSS <item> parsing for title + description."""

    def test_plain_title_and_description(self) -> None:
        xml = (
            "<item>"
            "<title>Treasury sanctions Russia entities</title>"
            "<description>New measures applied</description>"
            "</item>"
        )
        items = _parse_rss_items(xml)
        assert len(items) == 1
        assert items[0]["title"] == "Treasury sanctions Russia entities"
        assert items[0]["description"] == "New measures applied"

    def test_cdata_wrapped_title(self) -> None:
        xml = (
            "<item>"
            "<title><![CDATA[OFAC: Iran asset freeze]]></title>"
            "<description></description>"
            "</item>"
        )
        items = _parse_rss_items(xml)
        assert items[0]["title"] == "OFAC: Iran asset freeze"

    def test_multiple_items(self) -> None:
        xml = (
            "<item><title>Item 1</title><description>d1</description></item>"
            "<item><title>Item 2</title><description>d2</description></item>"
        )
        assert len(_parse_rss_items(xml)) == 2


# ── _count_hits ────────────────────────────────────────────────────────────────

class TestCountHits:
    """Verify country keyword matching against RSS items."""

    def test_exact_keyword_match(self) -> None:
        items = [{"title": "Russia sanctions update", "description": ""}]
        assert _count_hits("RU", items) == 1

    def test_case_insensitive_match(self) -> None:
        items = [{"title": "RUSSIA new trade restrictions", "description": ""}]
        assert _count_hits("RU", items) == 1

    def test_no_match_returns_zero(self) -> None:
        items = [{"title": "Unrelated trade news", "description": ""}]
        assert _count_hits("JP", items) == 0

    def test_unknown_iso2_returns_zero(self) -> None:
        items = [{"title": "Some title", "description": ""}]
        assert _count_hits("XX", items) == 0


# ── _sanctions_score ───────────────────────────────────────────────────────────

class TestSanctionsScore:
    """Verify score computation for sanctioned and non-sanctioned countries."""

    def test_sanctioned_country_minimum_floor(self) -> None:
        score, reason = _sanctions_score("KP", 0)  # North Korea
        assert score == 10
        assert reason == "sanctioned_country"

    def test_dynamic_score_beats_baseline(self) -> None:
        # Russia baseline=9, but 5 dynamic hits → min(8 + (5-3), 10) = 10
        score, reason = _sanctions_score("RU", 5)
        assert score == 10

    def test_low_hit_country_scores_dynamically(self) -> None:
        score, _ = _sanctions_score("JP", 2)  # 1-2 hits → 4 + min(1, 3) = 5
        assert score == 5

    def test_zero_hits_non_sanctioned_scores_zero(self) -> None:
        score, _ = _sanctions_score("JP", 0)
        assert score == 0

    def test_score_capped_at_10(self) -> None:
        score, _ = _sanctions_score("JP", 100)
        assert score <= 10


# ── collect_compliance_signals ─────────────────────────────────────────────────

class TestCollectComplianceSignals:
    """Integration tests with mocked HTTP."""

    def test_returns_records_for_all_regions(self) -> None:
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.compliance.get_text", return_value="<rss></rss>"),
            patch("collectors.compliance.put_signal"),
        ):
            records = collect_compliance_signals()

        assert len(records) == len(ALL_REGIONS)
        for r in records:
            assert r.signal_class.value == "F"
            assert 0 <= r.score <= 10

    def test_ofac_rss_failure_uses_static_baseline(self) -> None:
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.compliance.get_text", side_effect=RuntimeError("down")),
            patch("collectors.compliance.put_signal"),
        ):
            records = collect_compliance_signals()

        assert len(records) == len(ALL_REGIONS)
        # North Korea has baseline 10 regardless of RSS availability
        kp_records = [r for r in records if r.raw_data.get("iso2") == "KP"]
        for r in kp_records:
            assert r.score == 10

    def test_eu_oj_rss_failure_does_not_crash(self) -> None:
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.compliance.get_text", return_value="<rss></rss>"),
            patch("collectors.compliance._fetch_eu_oj_items", side_effect=RuntimeError("eu down")),
            patch("collectors.compliance.put_signal"),
        ):
            # Should not raise; EU failure is gracefully handled
            records = collect_compliance_signals()

        assert len(records) == len(ALL_REGIONS)
