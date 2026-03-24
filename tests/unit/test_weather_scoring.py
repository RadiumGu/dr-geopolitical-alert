"""Unit tests for weather collector scoring functions."""
from __future__ import annotations

import pytest

from collectors.weather import _haversine_km, _score_earthquake, _score_weather
from shared.region_config import RegionConfig


# ── _haversine_km ─────────────────────────────────────────────────────────────

class TestHaversineKm:
    """Verify great-circle distance calculations against known values."""

    def test_same_point_is_zero(self) -> None:
        assert _haversine_km(35.0, 139.0, 35.0, 139.0) == pytest.approx(0.0, abs=0.1)

    def test_tokyo_to_osaka(self) -> None:
        # Tokyo (35.68, 139.69) → Osaka (34.69, 135.50) ≈ 400 km
        dist = _haversine_km(35.68, 139.69, 34.69, 135.50)
        assert 390 < dist < 420

    def test_north_south_pole(self) -> None:
        # Distance from equator to North Pole along meridian ≈ 10_008 km
        dist = _haversine_km(0.0, 0.0, 90.0, 0.0)
        assert 9_900 < dist < 10_100

    def test_symmetry(self) -> None:
        d1 = _haversine_km(35.0, 139.0, 1.35, 103.8)
        d2 = _haversine_km(1.35, 103.8, 35.0, 139.0)
        assert d1 == pytest.approx(d2, rel=1e-6)


# ── _score_weather ─────────────────────────────────────────────────────────────

def _weather_data(
    temps: list[float],
    precips: list[float],
    winds: list[float],
) -> dict:
    """Build a minimal Open-Meteo-style response dict."""
    return {
        "hourly": {
            "temperature_2m": temps,
            "precipitation": precips,
            "wind_speed_10m": winds,
            "time": [f"2026-01-01T{h:02d}:00" for h in range(len(temps))],
        }
    }


class TestScoreWeather:
    """Test _score_weather against scoring thresholds."""

    def test_normal_conditions_score_zero(self) -> None:
        data = _weather_data([25.0, 26.0], [0.0, 0.5], [10.0, 15.0])
        score, details = _score_weather(data)
        assert score == 0
        assert details["alerts"] == []

    def test_extreme_heat_adds_8(self) -> None:
        data = _weather_data([45.0], [0.0], [0.0])
        score, details = _score_weather(data)
        assert score == 8
        assert any("extreme_heat" in a for a in details["alerts"])

    def test_moderate_heat_adds_3(self) -> None:
        data = _weather_data([41.0], [0.0], [0.0])
        score, details = _score_weather(data)
        assert score == 3

    def test_heavy_rain_adds_10(self) -> None:
        data = _weather_data([25.0], [20.0], [0.0])
        score, details = _score_weather(data)
        assert score == 10
        assert any("heavy_rain" in a for a in details["alerts"])

    def test_moderate_rain_adds_4(self) -> None:
        data = _weather_data([25.0], [10.0], [0.0])
        score, details = _score_weather(data)
        assert score == 4

    def test_strong_wind_adds_7(self) -> None:
        data = _weather_data([25.0], [0.0], [100.0])
        score, details = _score_weather(data)
        assert score == 7
        assert any("strong_wind" in a for a in details["alerts"])

    def test_multiple_hazards_capped_at_15(self) -> None:
        # extreme heat (+8) + heavy rain (+10) + strong wind (+7) = 25, capped at 15
        data = _weather_data([46.0], [25.0], [110.0])
        score, _ = _score_weather(data)
        assert score == 15

    def test_empty_data_score_zero(self) -> None:
        score, _ = _score_weather({"hourly": {}})
        assert score == 0


# ── _score_earthquake ──────────────────────────────────────────────────────────

def _make_region(lat: float, lon: float) -> RegionConfig:
    """Build a minimal RegionConfig for testing."""
    return RegionConfig(
        code="test-region",
        city="TestCity",
        lat=lat,
        lon=lon,
        country="XX",
        baseline=10,
        dr_target="us-east-1",
    )


def _quake(lat: float, lon: float, mag: float, place: str = "Test") -> dict:
    """Build a minimal USGS earthquake feature dict."""
    return {
        "geometry": {"coordinates": [lon, lat, 10.0]},
        "properties": {"mag": mag, "place": place},
    }


class TestScoreEarthquake:
    """Test _score_earthquake proximity and magnitude thresholds."""

    def test_no_quakes_score_zero(self) -> None:
        region = _make_region(35.68, 139.69)
        score, details = _score_earthquake(region, [])
        assert score == 0
        assert details["nearest_quake"] is None

    def test_far_quake_over_200km_ignored(self) -> None:
        region = _make_region(35.68, 139.69)
        # ~500 km away from Tokyo
        quakes = [_quake(31.0, 131.0, 6.0, "Far away")]
        score, details = _score_earthquake(region, quakes)
        assert score == 0

    def test_m4_within_200km_scores_5(self) -> None:
        region = _make_region(35.68, 139.69)
        # ~156 km WNW of Tokyo — within 200 km but outside 100 km
        quakes = [_quake(36.0, 138.0, 4.5, "Nearby M4")]
        score, _ = _score_earthquake(region, quakes)
        assert score == 5

    def test_m5_within_100km_scores_15(self) -> None:
        region = _make_region(35.68, 139.69)
        # ~50 km away from Tokyo
        quakes = [_quake(35.2, 139.1, 5.5, "Close M5")]
        score, details = _score_earthquake(region, quakes)
        assert score == 15
        assert details["nearest_quake"] is not None
        assert details["nearest_quake"]["mag"] == 5.5

    def test_m5_at_101km_scores_only_5(self) -> None:
        """M5 quake just beyond 100 km should score 5, not 15."""
        region = _make_region(35.68, 139.69)
        # ~110 km SE of Tokyo
        quakes = [_quake(34.65, 140.55, 5.2, "M5 outside 100km")]
        score, _ = _score_earthquake(region, quakes)
        assert score == 5

    def test_multiple_quakes_takes_max_score(self) -> None:
        region = _make_region(35.68, 139.69)
        quakes = [
            _quake(34.5, 138.0, 4.5, "Far M4"),   # within 200km → score 5
            _quake(35.2, 139.1, 5.5, "Close M5"),  # within 100km → score 15
        ]
        score, _ = _score_earthquake(region, quakes)
        assert score == 15

    def test_score_capped_at_15(self) -> None:
        region = _make_region(35.68, 139.69)
        quakes = [_quake(35.2, 139.1, 8.0, "Mega quake")]
        score, _ = _score_earthquake(region, quakes)
        assert score == 15
