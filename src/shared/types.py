"""Data models for the DR Geopolitical Alert system."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SignalClass(str, Enum):
    """Seven signal categories mapped to GPRI dimensions."""
    A = "A"  # Armed conflict
    B = "B"  # Cyber threats
    C = "C"  # Political stability
    D = "D"  # Physical infrastructure
    E = "E"  # Extreme weather
    F = "F"  # Compliance / regulatory
    G = "G"  # BGP / backbone anomalies


class GpriLevel(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"
    BLACK = "BLACK"


# Maximum score per dimension
MAX_SCORES: dict[SignalClass, int] = {
    SignalClass.A: 20,
    SignalClass.B: 15,
    SignalClass.C: 15,
    SignalClass.D: 10,
    SignalClass.E: 15,
    SignalClass.F: 10,
    SignalClass.G: 15,
}

LEVEL_THRESHOLDS: list[tuple[int, int, GpriLevel]] = [
    (0, 30, GpriLevel.GREEN),
    (31, 50, GpriLevel.YELLOW),
    (51, 70, GpriLevel.ORANGE),
    (71, 85, GpriLevel.RED),
    (86, 100, GpriLevel.BLACK),
]


def gpri_to_level(score: int) -> GpriLevel:
    """Convert a GPRI score (0-100) to an alert level."""
    for low, high, level in LEVEL_THRESHOLDS:
        if low <= score <= high:
            return level
    return GpriLevel.BLACK  # score > 100 edge case


@dataclass
class RegionConfig:
    """Static configuration for a single AWS Region."""
    code: str           # e.g. "me-central-1"
    city: str           # e.g. "Dubai"
    lat: float
    lon: float
    country: str        # ISO 3166-1 alpha-2
    baseline: int       # Static GPRI baseline (0-25)
    dr_target: str      # Recommended DR Region code
    cables: list[str] = field(default_factory=list)  # Associated submarine cables


@dataclass
class SignalRecord:
    """A single signal measurement for one region + dimension."""
    region: str
    signal_class: SignalClass
    score: int
    raw_data: dict[str, Any]
    source: str
    collected_at: str  # ISO 8601

    @property
    def pk(self) -> str:
        return f"REGION#{self.region}"

    @property
    def sk(self) -> str:
        return f"SIG#{self.signal_class.value}#{self.collected_at}"


@dataclass
class GpriRecord:
    """A GPRI score snapshot for one region."""
    region: str
    gpri: int
    level: GpriLevel
    prev_level: GpriLevel | None
    components: dict[str, int]  # {"A": 8, "B": 3, ...}
    baseline: int
    compliance_block: bool
    timestamp: str  # ISO 8601

    @property
    def pk(self) -> str:
        return f"REGION#{self.region}"

    @property
    def sk(self) -> str:
        return f"TS#{self.timestamp}"
