"""Unit tests for GPRI calculator: gpri_to_level thresholds and scoring logic."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from shared.types import GpriLevel, SignalClass, gpri_to_level


# ── gpri_to_level boundary tests ──────────────────────────────────────────────

class TestGpriToLevel:
    """Verify each threshold boundary for gpri_to_level."""

    @pytest.mark.parametrize("score,expected", [
        (0,   GpriLevel.GREEN),
        (30,  GpriLevel.GREEN),
        (31,  GpriLevel.YELLOW),
        (50,  GpriLevel.YELLOW),
        (51,  GpriLevel.ORANGE),
        (70,  GpriLevel.ORANGE),
        (71,  GpriLevel.RED),
        (85,  GpriLevel.RED),
        (86,  GpriLevel.BLACK),
        (100, GpriLevel.BLACK),
    ])
    def test_boundary(self, score: int, expected: GpriLevel) -> None:
        assert gpri_to_level(score) == expected

    def test_above_100_returns_black(self) -> None:
        assert gpri_to_level(101) == GpriLevel.BLACK


# ── _calc_gpri scoring logic ───────────────────────────────────────────────────

def _zero_signals() -> dict[str, int]:
    """Return a dict with all signal classes set to zero."""
    return {cls.value: 0 for cls in SignalClass}


class TestCalcGpri:
    """Test _calc_gpri with mocked DynamoDB calls."""

    def test_baseline_only(self) -> None:
        """When all signals are 0, GPRI equals the baseline."""
        signals = _zero_signals()
        with (
            patch("engine.gpri_calculator.get_latest_signals", return_value=signals),
            patch("engine.gpri_calculator.get_previous_level", return_value=None),
            patch("engine.gpri_calculator.get_baseline_delta", return_value=0),
        ):
            from engine.gpri_calculator import _calc_gpri
            record = _calc_gpri("ap-northeast-1", baseline=13)

        assert record.gpri == 13
        assert record.level == GpriLevel.GREEN
        assert record.prev_level is None

    def test_signals_add_to_baseline(self) -> None:
        """Signal scores are summed together with the baseline."""
        signals = _zero_signals()
        signals["A"] = 10
        signals["E"] = 8
        with (
            patch("engine.gpri_calculator.get_latest_signals", return_value=signals),
            patch("engine.gpri_calculator.get_previous_level", return_value=None),
            patch("engine.gpri_calculator.get_baseline_delta", return_value=0),
        ):
            from engine.gpri_calculator import _calc_gpri
            record = _calc_gpri("us-east-1", baseline=5)

        assert record.gpri == 23  # 5 + 10 + 8
        assert record.level == GpriLevel.GREEN

    def test_gpri_capped_at_100(self) -> None:
        """GPRI must never exceed 100 regardless of inputs."""
        signals = {cls.value: 15 for cls in SignalClass}
        signals["A"] = 20
        with (
            patch("engine.gpri_calculator.get_latest_signals", return_value=signals),
            patch("engine.gpri_calculator.get_previous_level", return_value=None),
            patch("engine.gpri_calculator.get_baseline_delta", return_value=0),
        ):
            from engine.gpri_calculator import _calc_gpri
            record = _calc_gpri("eu-west-1", baseline=25)

        assert record.gpri == 100

    def test_level_orange_at_55(self) -> None:
        """Score of 55 maps to ORANGE level."""
        signals = _zero_signals()
        signals["A"] = 20
        signals["B"] = 15
        with (
            patch("engine.gpri_calculator.get_latest_signals", return_value=signals),
            patch("engine.gpri_calculator.get_previous_level", return_value=None),
            patch("engine.gpri_calculator.get_baseline_delta", return_value=0),
        ):
            from engine.gpri_calculator import _calc_gpri
            record = _calc_gpri("ap-southeast-1", baseline=20)

        assert record.gpri == 55
        assert record.level == GpriLevel.ORANGE

    def test_compliance_block_triggered_at_8(self) -> None:
        """F-class score >= 8 sets compliance_block to True."""
        signals = _zero_signals()
        signals["F"] = 8
        with (
            patch("engine.gpri_calculator.get_latest_signals", return_value=signals),
            patch("engine.gpri_calculator.get_previous_level", return_value=None),
            patch("engine.gpri_calculator.get_baseline_delta", return_value=0),
        ):
            from engine.gpri_calculator import _calc_gpri
            record = _calc_gpri("ap-northeast-1", baseline=10)

        assert record.compliance_block is True

    def test_compliance_block_not_triggered_at_7(self) -> None:
        """F-class score of 7 does NOT trigger compliance_block."""
        signals = _zero_signals()
        signals["F"] = 7
        with (
            patch("engine.gpri_calculator.get_latest_signals", return_value=signals),
            patch("engine.gpri_calculator.get_previous_level", return_value=None),
            patch("engine.gpri_calculator.get_baseline_delta", return_value=0),
        ):
            from engine.gpri_calculator import _calc_gpri
            record = _calc_gpri("ap-northeast-1", baseline=10)

        assert record.compliance_block is False

    def test_prev_level_stored(self) -> None:
        """Previous level returned from DB is stored on the record."""
        signals = _zero_signals()
        with (
            patch("engine.gpri_calculator.get_latest_signals", return_value=signals),
            patch("engine.gpri_calculator.get_previous_level", return_value=GpriLevel.YELLOW),
            patch("engine.gpri_calculator.get_baseline_delta", return_value=0),
        ):
            from engine.gpri_calculator import _calc_gpri
            record = _calc_gpri("ap-northeast-1", baseline=5)

        assert record.prev_level == GpriLevel.YELLOW

    def test_components_stored(self) -> None:
        """Signal components are stored on the record as-is."""
        signals = _zero_signals()
        signals["A"] = 7
        signals["B"] = 3
        with (
            patch("engine.gpri_calculator.get_latest_signals", return_value=signals),
            patch("engine.gpri_calculator.get_previous_level", return_value=None),
            patch("engine.gpri_calculator.get_baseline_delta", return_value=0),
        ):
            from engine.gpri_calculator import _calc_gpri
            record = _calc_gpri("ap-northeast-1", baseline=5)

        assert record.components["A"] == 7
        assert record.components["B"] == 3

    def test_baseline_delta_applied(self) -> None:
        """Dynamic baseline delta is added to static baseline."""
        signals = _zero_signals()
        with (
            patch("engine.gpri_calculator.get_latest_signals", return_value=signals),
            patch("engine.gpri_calculator.get_previous_level", return_value=None),
            patch("engine.gpri_calculator.get_baseline_delta", return_value=3),
        ):
            from engine.gpri_calculator import _calc_gpri
            record = _calc_gpri("ap-northeast-1", baseline=10)

        assert record.gpri == 13  # 10 + 3
        assert record.baseline == 13  # effective baseline stored
