"""Unit tests for A-class conflict collector."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from collectors.conflict import (
    GDELT_COUNTRY_MAP,
    NEIGHBOR_MAP,
    _anomaly_score,
    _build_country_timeseries,
    _merge_timeseries,
    _parse_gdelt_date,
    collect_conflict_signals,
)


# ── _anomaly_score ─────────────────────────────────────────────────────────────

class TestAnomalyScore:
    """Verify scoring thresholds for conflict anomaly ratio."""

    def test_no_history_uses_event_count(self) -> None:
        # daily_avg_90d == 0 → min(count_7d * 2, 10)
        assert _anomaly_score(3, 0.0) == 6
        assert _anomaly_score(6, 0.0) == 10  # capped at 10

    def test_below_1_5_ratio_low_score(self) -> None:
        # ratio 7/7 = 1.0, < 1.5 → low score (≤5)
        score = _anomaly_score(7, 1.0)
        assert 0 <= score <= 5

    def test_ratio_1_5_to_3_medium_score(self) -> None:
        # ratio = 14/(1.0*7) = 2.0 → 6-10
        score = _anomaly_score(14, 1.0)
        assert 6 <= score <= 10

    def test_ratio_gte_3_high_score(self) -> None:
        # ratio = 21/(1.0*7) = 3.0 → 15+
        score = _anomaly_score(21, 1.0)
        assert score >= 15

    def test_score_capped_at_20(self) -> None:
        # Extremely high ratio
        score = _anomaly_score(1000, 1.0)
        assert score <= 20


# ── _parse_gdelt_date ──────────────────────────────────────────────────────────

class TestParseGdeltDate:
    """Verify GDELT seendate parsing."""

    def test_standard_format(self) -> None:
        assert _parse_gdelt_date("20260325T043000Z") == "2026-03-25"

    def test_exact_8_chars(self) -> None:
        assert _parse_gdelt_date("20260101") == "2026-01-01"

    def test_short_string_returns_empty(self) -> None:
        assert _parse_gdelt_date("2026") == ""
        assert _parse_gdelt_date("") == ""


# ── GDELT_COUNTRY_MAP ──────────────────────────────────────────────────────────

class TestGdeltCountryMap:
    """Verify GDELT_COUNTRY_MAP covers key countries."""

    def test_monitored_countries_present(self) -> None:
        expected = {
            "Israel": "IL",
            "United Arab Emirates": "AE",
            "Bahrain": "BH",
            "South Korea": "KR",
            "Japan": "JP",
            "India": "IN",
            "United States": "US",
        }
        for name, iso2 in expected.items():
            assert GDELT_COUNTRY_MAP.get(name) == iso2, f"Missing: {name}"

    def test_neighbor_countries_present(self) -> None:
        for name in ["Yemen", "Syria", "Iran", "Iraq", "Pakistan", "North Korea", "Myanmar"]:
            assert name in GDELT_COUNTRY_MAP, f"Neighbor missing: {name}"


# ── _build_country_timeseries ──────────────────────────────────────────────────

class TestBuildCountryTimeseries:
    """Verify UCDP, ACLED, and GDELT event parsing."""

    def test_ucdp_parses_country_id_and_date(self) -> None:
        events = [
            {"country_id": "JP", "date_start": "2026-03-20"},
            {"country_id": "JP", "date_start": "2026-03-21"},
            {"country_id": "KR", "date_start": "2026-03-20"},
        ]
        series = _build_country_timeseries(events, "ucdp")
        assert len(series["JP"]) == 2
        assert len(series["KR"]) == 1

    def test_acled_parses_iso_and_event_date(self) -> None:
        events = [
            {"iso": "DE", "event_date": "2026-03-20"},
            {"iso": "DE", "event_date": "2026-03-21"},
        ]
        series = _build_country_timeseries(events, "acled")
        assert len(series["DE"]) == 2

    def test_gdelt_maps_country_name_and_seendate(self) -> None:
        events = [
            {"sourcecountry": "Israel", "seendate": "20260320T120000Z"},
            {"sourcecountry": "Israel", "seendate": "20260321T080000Z"},
            {"sourcecountry": "Yemen", "seendate": "20260320T090000Z"},
        ]
        series = _build_country_timeseries(events, "gdelt")
        assert series["IL"] == ["2026-03-20", "2026-03-21"]
        assert series["YE"] == ["2026-03-20"]

    def test_gdelt_unknown_country_skipped(self) -> None:
        events = [{"sourcecountry": "Atlantis", "seendate": "20260320T000000Z"}]
        series = _build_country_timeseries(events, "gdelt")
        assert series == {}

    def test_missing_fields_skipped(self) -> None:
        events = [{"country_id": "", "date_start": "2026-03-20"}]
        series = _build_country_timeseries(events, "ucdp")
        assert series == {}


# ── _merge_timeseries ──────────────────────────────────────────────────────────

class TestMergeTimeseries:
    """Verify merging of two country timeseries dicts."""

    def test_merges_disjoint_countries(self) -> None:
        a = {"IL": ["2026-03-20"]}
        b = {"YE": ["2026-03-21"]}
        merged = _merge_timeseries(a, b)
        assert merged["IL"] == ["2026-03-20"]
        assert merged["YE"] == ["2026-03-21"]

    def test_merges_overlapping_countries(self) -> None:
        a = {"IL": ["2026-03-20"]}
        b = {"IL": ["2026-03-21", "2026-03-22"]}
        merged = _merge_timeseries(a, b)
        assert sorted(merged["IL"]) == ["2026-03-20", "2026-03-21", "2026-03-22"]

    def test_empty_inputs(self) -> None:
        assert _merge_timeseries({}, {}) == {}
        assert _merge_timeseries({"IL": ["2026-03-20"]}, {})["IL"] == ["2026-03-20"]


# ── collect_conflict_signals ───────────────────────────────────────────────────

class TestCollectConflictSignals:
    """Integration-style tests with mocked HTTP and DB."""

    def test_returns_records_for_all_regions(self) -> None:
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.conflict._fetch_acled_events", side_effect=ValueError("no key")),
            patch("collectors.conflict._fetch_ucdp_events", side_effect=RuntimeError("401")),
            patch("collectors.conflict._fetch_gdelt_events", return_value=[]),
            patch("collectors.conflict.put_signal"),
        ):
            records = collect_conflict_signals()

        assert len(records) == len(ALL_REGIONS)
        for r in records:
            assert r.signal_class.value == "A"
            assert 0 <= r.score <= 20

    def test_score_zero_when_no_events(self) -> None:
        with (
            patch("collectors.conflict._fetch_acled_events", side_effect=ValueError("no key")),
            patch("collectors.conflict._fetch_ucdp_events", side_effect=RuntimeError("401")),
            patch("collectors.conflict._fetch_gdelt_events", return_value=[]),
            patch("collectors.conflict.put_signal"),
        ):
            records = collect_conflict_signals()

        for r in records:
            assert r.score == 0

    def test_gdelt_fallback_used_when_ucdp_fails(self) -> None:
        """GDELT is used alone when both keyed ACLED and UCDP fail."""
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.conflict._fetch_acled_events", side_effect=ValueError("no key")),
            patch("collectors.conflict._fetch_ucdp_events", side_effect=RuntimeError("401")),
            patch("collectors.conflict._fetch_gdelt_events", return_value=[]) as mock_gdelt,
            patch("collectors.conflict.put_signal"),
        ):
            records = collect_conflict_signals()

        mock_gdelt.assert_called_once()
        assert len(records) == len(ALL_REGIONS)
        for r in records:
            assert r.source == "gdelt"

    def test_ucdp_and_gdelt_merged_when_both_available(self) -> None:
        """When both UCDP and GDELT succeed, source_label is 'ucdp+gdelt'."""
        from shared.region_config import ALL_REGIONS
        from datetime import date, timedelta

        today = date.today()
        ucdp_events = [{"country_id": "IL", "date_start": today.isoformat()}]
        gdelt_articles = [
            {"sourcecountry": "Yemen", "seendate": today.strftime("%Y%m%d") + "T000000Z"}
        ]

        with (
            patch("collectors.conflict._fetch_acled_events", side_effect=ValueError("no key")),
            patch("collectors.conflict._fetch_ucdp_events", return_value=ucdp_events),
            patch("collectors.conflict._fetch_gdelt_events", return_value=gdelt_articles),
            patch("collectors.conflict.put_signal"),
        ):
            records = collect_conflict_signals()

        assert len(records) == len(ALL_REGIONS)
        for r in records:
            assert r.source == "ucdp+gdelt"

    def test_acled_used_when_credentials_available(self) -> None:
        """Keyed ACLED takes priority when credentials work."""
        from shared.region_config import ALL_REGIONS

        acled_events: list[dict] = []
        with (
            patch("collectors.conflict._fetch_acled_events", return_value=acled_events),
            patch("collectors.conflict._fetch_ucdp_events") as mock_ucdp,
            patch("collectors.conflict._fetch_gdelt_events") as mock_gdelt,
            patch("collectors.conflict.put_signal"),
        ):
            records = collect_conflict_signals()

        mock_ucdp.assert_not_called()
        mock_gdelt.assert_not_called()
        for r in records:
            assert r.source == "acled"


class TestNeighborSpillover:
    """Verify neighbor spillover raises AE score when Yemen is active."""

    def test_ae_score_elevated_by_yemen_conflict(self) -> None:
        """AE (UAE) should inherit a nonzero score from YE (Yemen) spillover."""
        from datetime import date, timedelta

        # Simulate heavy Yemen conflict in last 7 days
        today = date.today()
        ye_dates = [(today - timedelta(days=i)).isoformat() for i in range(7)]
        ye_dates_90d = ye_dates * 5  # 35 events across last 7 days

        acled_events = [
            {"iso": "YE", "event_date": d} for d in ye_dates_90d
        ]

        with (
            patch("collectors.conflict._fetch_acled_events", return_value=acled_events),
            patch("collectors.conflict.put_signal"),
        ):
            records = collect_conflict_signals()

        ae_records = [r for r in records if r.region == "me-central-1"]
        assert ae_records, "me-central-1 record missing"
        ae = ae_records[0]
        # AE itself has no events, but Yemen spillover (500km, decay=0.5) should raise score
        assert ae.score > 0, f"Expected spillover score > 0 for AE, got {ae.score}"
        assert "spillover" in ae.raw_data

    def test_neighbor_map_kp_affects_kr(self) -> None:
        """Both KR regions should show KP in their NEIGHBOR_MAP."""
        assert "KR" in NEIGHBOR_MAP
        kp_entries = [n for n, _ in NEIGHBOR_MAP["KR"] if n == "KP"]
        assert kp_entries, "KP not in KR neighbors"

    def test_spillover_capped_at_max_score(self) -> None:
        """Spillover score must not exceed MAX_SCORE=20."""
        from datetime import date, timedelta

        today = date.today()
        # Massive conflict in neighbor
        neighbor_dates = [(today - timedelta(days=i)).isoformat() for i in range(7)] * 100

        acled_events = [{"iso": "YE", "event_date": d} for d in neighbor_dates]

        with (
            patch("collectors.conflict._fetch_acled_events", return_value=acled_events),
            patch("collectors.conflict.put_signal"),
        ):
            records = collect_conflict_signals()

        for r in records:
            assert r.score <= 20
