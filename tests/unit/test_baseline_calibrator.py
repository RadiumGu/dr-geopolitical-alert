"""Unit tests for weekly baseline calibrator."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from engine.baseline_calibrator import calibrate_region, handler, DAMPING, MAX_DELTA


class TestCalibrateRegion:
    """Test the core calibration logic."""

    @patch("engine.baseline_calibrator.get_signal_scores_for_calibration")
    def test_delta_calculation_basic(self, mock_scores) -> None:
        """Signal median sum above baseline → positive delta."""
        # Each class has median=3, 7 classes → sum=21, baseline=10
        # deviation=11, damped=3.3, rounded=3
        mock_scores.return_value = {
            "A": [3] * 100, "B": [3] * 100, "C": [3] * 100,
            "D": [3] * 100, "E": [3] * 100, "F": [3] * 100, "G": [3] * 100,
        }
        result = calibrate_region("ap-south-1", static_baseline=10, current_delta=0)
        assert result["new_delta"] == 3
        assert result["changed"] is True

    @patch("engine.baseline_calibrator.get_signal_scores_for_calibration")
    def test_delta_clamped_to_max(self, mock_scores) -> None:
        """Delta should be clamped to ±MAX_DELTA."""
        # Each class median=10, sum=70, baseline=2
        # deviation=68, damped=20.4 → clamped to +5
        mock_scores.return_value = {
            "A": [10] * 100, "B": [10] * 100, "C": [10] * 100,
            "D": [10] * 100, "E": [10] * 100, "F": [10] * 100, "G": [10] * 100,
        }
        result = calibrate_region("us-west-2", static_baseline=2, current_delta=0)
        assert result["new_delta"] == MAX_DELTA
        assert result["new_delta"] == 5

    @patch("engine.baseline_calibrator.get_signal_scores_for_calibration")
    def test_delta_clamped_negative(self, mock_scores) -> None:
        """Negative deviation → negative delta, clamped to -MAX_DELTA."""
        # All zeros, baseline=25, deviation=-25, damped=-7.5 → clamped -5
        mock_scores.return_value = {
            "A": [0] * 100, "B": [0] * 100, "C": [0] * 100,
            "D": [0] * 100, "E": [0] * 100, "F": [0] * 100, "G": [0] * 100,
        }
        result = calibrate_region("il-central-1", static_baseline=25, current_delta=0)
        assert result["new_delta"] == -MAX_DELTA
        assert result["new_delta"] == -5

    @patch("engine.baseline_calibrator.get_signal_scores_for_calibration")
    def test_damping_coefficient(self, mock_scores) -> None:
        """Verify damping: deviation * 0.3."""
        # Median sum=10, baseline=10 → deviation=0 → delta=0
        mock_scores.return_value = {
            "A": [2] * 100, "B": [1] * 100, "C": [2] * 100,
            "D": [1] * 100, "E": [2] * 100, "F": [1] * 100, "G": [1] * 100,
        }
        # sum of medians = 2+1+2+1+2+1+1 = 10, baseline=10, deviation=0
        result = calibrate_region("test-region", static_baseline=10, current_delta=0)
        assert result["new_delta"] == 0
        assert result["changed"] is False

    @patch("engine.baseline_calibrator.get_signal_scores_for_calibration")
    def test_no_change_when_delta_same(self, mock_scores) -> None:
        """If calculated delta equals current delta → changed=False."""
        # sum=13, baseline=10, deviation=3, damped=0.9 → rounded=1
        mock_scores.return_value = {
            "A": [2] * 100, "B": [2] * 100, "C": [2] * 100,
            "D": [2] * 100, "E": [2] * 100, "F": [2] * 100, "G": [1] * 100,
        }
        result = calibrate_region("test-region", static_baseline=10, current_delta=1)
        assert result["new_delta"] == 1
        assert result["changed"] is False

    @patch("engine.baseline_calibrator.get_signal_scores_for_calibration")
    def test_insufficient_data_keeps_current(self, mock_scores) -> None:
        """Too few samples → keep existing delta, don't recalibrate."""
        mock_scores.return_value = {
            "A": [5, 5], "B": [], "C": [], "D": [], "E": [], "F": [], "G": [],
        }
        result = calibrate_region("test-region", static_baseline=10, current_delta=2)
        assert result["new_delta"] == 2  # Kept existing
        assert result["changed"] is False
        assert "insufficient_data" in result["reason"]

    @patch("engine.baseline_calibrator.get_signal_scores_for_calibration")
    def test_uses_median_not_mean(self, mock_scores) -> None:
        """Verify median is used — outliers shouldn't skew the result."""
        # 99 values of 1, one outlier of 100 → median=1, mean≈2
        vals = [1] * 99 + [100]
        mock_scores.return_value = {
            "A": vals, "B": [0] * 100, "C": [0] * 100,
            "D": [0] * 100, "E": [0] * 100, "F": [0] * 100, "G": [0] * 100,
        }
        # median_sum=1, baseline=2, deviation=-1, damped=-0.3 → rounded=0
        result = calibrate_region("test-region", static_baseline=2, current_delta=0)
        assert result["signal_median_sum"] == 1.0
        assert result["new_delta"] == 0


class TestHandler:
    """Test the Lambda handler."""

    @patch("engine.baseline_calibrator._publish_summary")
    @patch("engine.baseline_calibrator.put_baseline_delta")
    @patch("engine.baseline_calibrator.get_all_baseline_deltas", return_value={})
    @patch("engine.baseline_calibrator.get_signal_scores_for_calibration")
    def test_handler_processes_all_regions(
        self, mock_scores, mock_deltas, mock_put, mock_publish
    ) -> None:
        # Return minimal data to trigger insufficient_data path → no writes
        mock_scores.return_value = {
            "A": [], "B": [], "C": [], "D": [], "E": [], "F": [], "G": [],
        }
        result = handler({}, {})
        assert result["statusCode"] == 200
        assert result["body"]["regions_processed"] > 0
        assert result["body"]["deltas_changed"] == 0
        mock_put.assert_not_called()
        mock_publish.assert_not_called()

    @patch("engine.baseline_calibrator._publish_summary")
    @patch("engine.baseline_calibrator.put_baseline_delta")
    @patch("engine.baseline_calibrator.get_all_baseline_deltas", return_value={})
    @patch("engine.baseline_calibrator.get_signal_scores_for_calibration")
    def test_handler_publishes_on_change(
        self, mock_scores, mock_deltas, mock_put, mock_publish
    ) -> None:
        # High scores → will generate delta changes
        mock_scores.return_value = {
            "A": [10] * 100, "B": [10] * 100, "C": [10] * 100,
            "D": [10] * 100, "E": [10] * 100, "F": [10] * 100, "G": [10] * 100,
        }
        result = handler({}, {})
        assert result["statusCode"] == 200
        assert result["body"]["deltas_changed"] > 0
        mock_publish.assert_called_once()
