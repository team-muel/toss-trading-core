"""Legacy alpha API backed by the canonical research-language implementation.

Pure alpha expression and simulation behavior lives in :mod:`alpha_management`.
This module retains the historical settings name and the outer integration that
turns research positions into the legacy runtime's ``Signal`` proposals.
"""

from __future__ import annotations

from dataclasses import dataclass

from alpha_management.expression import (
    Alpha,
    AlphaPositions,
    AlphaSimulationSettings,
    simulate_cross_section,
)
from toss_trading.engines import Signal


@dataclass(frozen=True)
class SimulationSettings(AlphaSimulationSettings):
    """Backward-compatible settings plus legacy signal loss sizing."""

    region: str = "KOR"
    stop_loss_frac: float = 0.15

    def __post_init__(self) -> None:
        super().__post_init__()
        if not 0 <= self.stop_loss_frac <= 1:
            raise ValueError("stop_loss_frac must be in [0, 1]")


def to_signals(positions: AlphaPositions, *, min_weight: float = 1e-9) -> list[Signal]:
    """Adapt research-only positions to proposals for the gated legacy runtime."""
    settings = positions.settings
    stop_loss_frac = getattr(settings, "stop_loss_frac", 0.15)
    signals: list[Signal] = []
    for symbol, weight in positions.weights.items():
        if abs(weight) <= min_weight:
            continue
        signals.append(
            Signal(
                engine=positions.alpha,
                symbol_or_pair=symbol,
                side="BUY" if weight > 0 else "SELL",
                raw_score=float(positions.raw.get(symbol, 0.0)),
                adjusted_score=float(positions.neutralized.get(symbol, 0.0)),
                target_weight=weight / settings.book_size,
                expected_max_loss=abs(weight) * stop_loss_frac,
                reason_code=f"alpha:{positions.alpha}:{settings.neutralization}",
            )
        )
    signals.sort(key=lambda signal: signal.target_weight, reverse=True)
    return signals
