"""Unit tests for A-class conflict collector."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from collectors.conflict import (
    NEIGHBOR_MAP,
    _anomaly_score,
    _build_country_timeseries,
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


# ── _build_country_timeseries ──────────────────────────────────────────────────

class TestBuildCountryTimeseries:
    """Verify UCDP and ACLED event parsing."""

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

    def test_missing_fields_skipped(self) -> None:
        events = [{"country_id": "", "date_start": "2026-03-20"}]
        series = _build_country_timeseries(events, "ucdp")
        assert series == {}


# ── collect_conflict_signals ───────────────────────────────────────────────────

class TestCollectConflictSignals:
    """Integration-style tests with mocked HTTP and DB."""

    def test_returns_records_for_all_regions(self) -> None:
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.conflict._fetch_acled_events", side_effect=ValueError("no key")),
            patch("collectors.conflict._fetch_ucdp_events", side_effect=RuntimeError("401")),
            patch("collectors.conflict._fetch_acled_public", return_value=[]),
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
            patch("collectors.conflict._fetch_acled_public", return_value=[]),
            patch("collectors.conflict.put_signal"),
        ):
            records = collect_conflict_signals()

        for r in records:
            assert r.score == 0

    def test_public_acled_fallback_used_when_ucdp_fails(self) -> None:
        """Public ACLED is tried when both keyed ACLED and UCDP fail."""
        from shared.region_config import ALL_REGIONS

        with (
            patch("collectors.conflict._fetch_acled_events", side_effect=ValueError("no key")),
            patch("collectors.conflict._fetch_ucdp_events", side_effect=RuntimeError("401")),
            patch("collectors.conflict._fetch_acled_public", return_value=[]) as mock_pub,
            patch("collectors.conflict.put_signal"),
        ):
            records = collect_conflict_signals()

        mock_pub.assert_called_once()
        assert len(records) == len(ALL_REGIONS)


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
