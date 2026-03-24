"""Unit tests for D-class infrastructure collector."""
from __future__ import annotations

from collectors.infrastructure import handler


class TestInfrastructureHandler:
    """Verify D-class handler returns suspended status immediately."""

    def test_handler_returns_suspended(self) -> None:
        result = handler({}, {})
        assert result["statusCode"] == 200
        assert result["body"]["signal_class"] == "D"
        assert result["body"]["status"] == "suspended_gdelt_quality"

    def test_handler_writes_nothing(self) -> None:
        result = handler({}, {})
        assert result["body"]["collected"] == 0
        assert result["body"]["written"] == 0

    def test_handler_always_returns_dict(self) -> None:
        """Handler must return a dict regardless of event content."""
        for event in [{}, {"Records": []}, None]:
            result = handler(event, None)
            assert isinstance(result, dict)
            assert "statusCode" in result
