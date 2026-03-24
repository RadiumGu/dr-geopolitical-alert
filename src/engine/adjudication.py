"""Adjudication engine — multi-signal cross-validation for GPRI confidence.

PRD §5.3: "单一信号不触发高级别告警，需至少两类信号关联确认"

Rules:
1. If only 1 dimension contributes >50% of non-baseline score → confidence=LOW, level -1
2. If 2 dimensions both exceed 50% of their max weight → confidence=MEDIUM
3. If 3+ dimensions exceed 50% of their max weight → confidence=HIGH, eligible for level +1
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from shared.types import SignalClass, GpriLevel, MAX_SCORES, gpri_to_level


class Confidence(Enum):
    LOW = "LOW"         # Single signal dominant — downgrade
    MEDIUM = "MEDIUM"   # 2 corroborating signals
    HIGH = "HIGH"       # 3+ corroborating signals — eligible for upgrade


@dataclass
class AdjudicationResult:
    """Result of cross-signal adjudication."""
    original_gpri: int
    adjusted_gpri: int
    original_level: GpriLevel
    adjusted_level: GpriLevel
    confidence: Confidence
    dominant_signals: list[str]
    corroborating_count: int
    rationale: str


LEVEL_ORDER = [GpriLevel.GREEN, GpriLevel.YELLOW, GpriLevel.ORANGE, GpriLevel.RED, GpriLevel.BLACK]


def _level_shift(level: GpriLevel, delta: int) -> GpriLevel:
    """Shift a level by delta positions (clamped)."""
    idx = LEVEL_ORDER.index(level)
    new_idx = max(0, min(len(LEVEL_ORDER) - 1, idx + delta))
    return LEVEL_ORDER[new_idx]


def adjudicate(
    gpri: int,
    level: GpriLevel,
    components: dict[str, int],
    baseline: int,
) -> AdjudicationResult:
    """Apply multi-signal cross-validation rules.

    Args:
        gpri: Raw GPRI score (baseline + signals).
        level: Level derived from raw GPRI.
        components: Per-class scores, e.g. {"A": 8, "B": 3, ...}.
        baseline: Region baseline score.

    Returns:
        AdjudicationResult with potentially adjusted level.
    """
    non_baseline_total = gpri - baseline
    if non_baseline_total <= 0:
        return AdjudicationResult(
            original_gpri=gpri,
            adjusted_gpri=gpri,
            original_level=level,
            adjusted_level=level,
            confidence=Confidence.LOW,
            dominant_signals=[],
            corroborating_count=0,
            rationale="No active signals above baseline",
        )

    # Find which dimensions are "active" (>50% of their max score)
    active_signals = []
    for cls in SignalClass:
        score = components.get(cls.value, 0)
        max_score = MAX_SCORES[cls]
        if max_score > 0 and score > max_score * 0.5:
            active_signals.append(cls.value)

    # Find dominant signal (contributes >50% of non-baseline score)
    dominant = []
    for cls in SignalClass:
        score = components.get(cls.value, 0)
        if non_baseline_total > 0 and score > non_baseline_total * 0.5:
            dominant.append(cls.value)

    corroborating = len(active_signals)

    # Apply rules
    if len(dominant) >= 1 and corroborating <= 1:
        # Single signal dominance → LOW confidence → downgrade
        adjusted_level = _level_shift(level, -1)
        confidence = Confidence.LOW
        rationale = (
            f"Single signal dominance ({', '.join(dominant)}) "
            f"contributes >{50}% of non-baseline score. "
            f"Downgraded from {level.value} to {adjusted_level.value}."
        )
    elif corroborating >= 3:
        # 3+ corroborating signals → HIGH confidence → eligible for upgrade
        adjusted_level = _level_shift(level, 1) if level != GpriLevel.GREEN else level
        confidence = Confidence.HIGH
        rationale = (
            f"{corroborating} corroborating signals ({', '.join(active_signals)}). "
            f"High confidence."
        )
    elif corroborating == 2:
        # 2 signals → MEDIUM confidence → no change
        adjusted_level = level
        confidence = Confidence.MEDIUM
        rationale = (
            f"2 corroborating signals ({', '.join(active_signals)}). "
            f"Medium confidence, level unchanged."
        )
    else:
        adjusted_level = level
        confidence = Confidence.LOW
        rationale = "Insufficient corroborating signals."

    return AdjudicationResult(
        original_gpri=gpri,
        adjusted_gpri=gpri,  # Score stays same, only level adjusts
        original_level=level,
        adjusted_level=adjusted_level,
        confidence=confidence,
        dominant_signals=dominant,
        corroborating_count=corroborating,
        rationale=rationale,
    )
