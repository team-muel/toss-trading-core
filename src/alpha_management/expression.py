"""Alpha expressions and research simulation semantics.

This module deliberately stops at simulated position weights. It does not
produce `asset_management` order intents and has no dependency on execution.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from . import operators as ops

RawExpression = Callable[[Mapping[str, object]], Mapping[str, float]]
VALID_NEUTRALIZATION = frozenset({"none", "market", "group"})


@dataclass(frozen=True)
class AlphaSimulationSettings:
    """BRAIN-style research settings, not live portfolio policy."""

    universe: str
    book_size: float = 1.0
    region: str = "GLOBAL"
    delay: int = 1
    decay: int = 0
    neutralization: str = "market"
    truncation: float = 0.10
    long_only: bool = True

    def __post_init__(self) -> None:
        if not self.universe.strip() or not self.region.strip():
            raise ValueError("universe and region cannot be blank")
        if self.neutralization not in VALID_NEUTRALIZATION:
            raise ValueError(f"neutralization must be one of {sorted(VALID_NEUTRALIZATION)}")
        if self.delay < 0 or self.decay < 0:
            raise ValueError("delay and decay must be non-negative")
        if self.book_size <= 0:
            raise ValueError("book_size must be positive")
        if not 0 < self.truncation <= 1:
            raise ValueError("truncation must be in (0, 1]")


@dataclass(frozen=True)
class Alpha:
    """Named fast expression over repository-supplied datafields."""

    name: str
    expression: RawExpression
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("alpha name cannot be blank")

    def raw(self, context: Mapping[str, object]) -> dict[str, float]:
        return dict(self.expression(context))


@dataclass(frozen=True)
class AlphaPositions:
    """Research result for one alpha on one cross-section."""

    alpha: str
    raw: dict[str, float]
    neutralized: dict[str, float]
    weights: dict[str, float]
    settings: AlphaSimulationSettings
    groups: dict[str, str] = field(default_factory=dict)


def simulate_cross_section(
    alpha: Alpha,
    context: Mapping[str, object],
    settings: AlphaSimulationSettings,
    *,
    groups: Mapping[str, str] | None = None,
) -> AlphaPositions:
    """Transform a raw expression into research-only simulated weights."""

    raw = alpha.raw(context)
    winsorized = ops.winsorize(raw)

    if settings.neutralization == "market":
        neutralized = ops.group_neutralize(winsorized, {})
    elif settings.neutralization == "group":
        if not groups:
            raise ValueError("group neutralization requires a groups mapping")
        neutralized = ops.group_neutralize(winsorized, groups)
    else:
        neutralized = dict(winsorized)

    if settings.long_only:
        neutralized = {key: max(value, 0.0) for key, value in neutralized.items()}
        if not any(value > 0 for value in neutralized.values()):
            weights = {key: 0.0 for key in neutralized}
            return AlphaPositions(
                alpha.name, raw, neutralized, weights, settings, dict(groups or {})
            )

    weights = ops.truncate(neutralized, settings.truncation)
    weights = {key: value * settings.book_size for key, value in weights.items()}
    return AlphaPositions(
        alpha.name, raw, neutralized, weights, settings, dict(groups or {})
    )
