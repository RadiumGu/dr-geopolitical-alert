"""Unit tests for D-class infrastructure collector."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from collectors.infrastructure import handler


@patch("collectors.infrastructure.put_signal")
@patch("collectors.infrastructure._fetch_json", return_value=None)
class TestInfrastructureHandler:
    """Verify D-class handler collects via RIPE Atlas and returns correctly."""

    def test_handler_returns_signal_class_d(self, mock_fetch, mock_put) -> None:
        result = handler({}, {})
        assert result["statusCode"] == 200
        assert result["body"]["signal_class"] == "D"
        assert "status" not in result["body"]

    def test_handler_collects_from_region_map(self, mock_fetch, mock_put) -> None:
        result = handler({}, {})
        assert result["body"]["collected"] >= 0
        assert "written" in result["body"]

    def test_handler_always_returns_dict(self, mock_fetch, mock_put) -> None:
        """Handler must return a dict regardless of event content."""
        for event in [{}, {"Records": []}, None]:
            result = handler(event, None)
            assert isinstance(result, dict)
            assert "statusCode" in result
