"""Alpha expressions and simulation settings (research-only).

An ``Alpha`` wraps a *fast expression*: a callable that turns a datafield
cross-section into a raw signal cross-section.  ``SimulationSettings`` captures
the same knobs a research platform exposes (neutralization, decay, truncation,
book size) and ``simulate_cross_section`` turns a raw signal into position
weights.

The important integration detail: :func:`to_signals` emits this repository's own
``toss_trading.engines.Signal`` objects.  That means a BRAIN-style alpha plugs
straight into the existing ``RiskHub`` and order-planner path without changing
any of their code, and every downstream gate (kill switch, reconciliation,
``live_trading_enabled``) still applies.  This layer never enables live trading.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from toss_trading.engines import Signal

from . import operators as ops

RawExpression = Callable[[Mapping[str, object]], Mapping[str, float]]

VALID_NEUTRALIZATION = frozenset({"none", "market", "group"})


@dataclass(frozen=True)
class SimulationSettings:
    """Research simulation knobs, mirroring a BRAIN alpha's settings.

    ``region``/``universe``/``delay`` are descriptive metadata for reproducible
    research.  ``neutralization``, ``truncation`` and ``book_size`` actively
    shape the position vector produced by :func:`simulate_cross_section`.
    """

    universe: str
    book_size: float
    region: str = "KOR"
    delay: int = 1
    decay: int = 0
    neutralization: str = "market"
    truncation: float = 0.10
    long_only: bool = True
    stop_loss_frac: float = 0.15

    def __post_init__(self) -> None:
        if self.neutralization not in VALID_NEUTRALIZATION:
            raise ValueError(f"neutralization must be one of {sorted(VALID_NEUTRALIZATION)}")
        if self.delay < 0:
            raise ValueError("delay must be non-negative")
        if self.book_size <= 0:
            raise ValueError("book_size must be positive")
        if not 0 < self.truncation <= 1:
            raise ValueError("truncation must be in (0, 1]")
        if not 0 <= self.stop_loss_frac <= 1:
            raise ValueError("stop_loss_frac must be in [0, 1]")


@dataclass(frozen=True)
class Alpha:
    """A named fast expression over datafields."""

    name: str
    expression: RawExpression
    description: str = ""

    def raw(self, context: Mapping[str, object]) -> dict[str, float]:
        return dict(self.expression(context))


@dataclass(frozen=True)
class AlphaPositions:
    """Result of simulating one alpha on one cross-section."""

    alpha: str
    raw: dict[str, float]
    neutralized: dict[str, float]
    weights: dict[str, float]
    settings: SimulationSettings
    groups: dict[str, str] = field(default_factory=dict)


def simulate_cross_section(
    alpha: Alpha,
    context: Mapping[str, object],
    settings: SimulationSettings,
    *,
    groups: Mapping[str, str] | None = None,
) -> AlphaPositions:
    """Turn a raw alpha signal into truncated, neutralized position weights."""
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
            return AlphaPositions(alpha.name, raw, neutralized, weights, settings, dict(groups or {}))

    weights = ops.truncate(neutralized, settings.truncation)
    weights = {key: value * settings.book_size for key, value in weights.items()}
    return AlphaPositions(alpha.name, raw, neutralized, weights, settings, dict(groups or {}))


def to_signals(positions: AlphaPositions, *, min_weight: float = 1e-9) -> list[Signal]:
    """Convert alpha positions into this repo's ``Signal`` objects.

    Long-only positive weights become ``BUY`` proposals; negative weights (only
    possible when ``long_only`` is disabled for research) become ``SELL``.  Zero
    weights are dropped.  ``expected_max_loss`` is a conservative notional stop
    so ``RiskHub`` can size the single-trade loss gate.
    """
    settings = positions.settings
    signals: list[Signal] = []
    for symbol, weight in positions.weights.items():
        if abs(weight) <= min_weight:
            continue
        side = "BUY" if weight > 0 else "SELL"
        expected_max_loss = abs(weight) * settings.stop_loss_frac
        signals.append(
            Signal(
                engine=positions.alpha,
                symbol_or_pair=symbol,
                side=side,
                raw_score=float(positions.raw.get(symbol, 0.0)),
                adjusted_score=float(positions.neutralized.get(symbol, 0.0)),
                target_weight=weight / settings.book_size,
                expected_max_loss=expected_max_loss,
                reason_code=f"alpha:{positions.alpha}:{settings.neutralization}",
            )
        )
    signals.sort(key=lambda signal: signal.target_weight, reverse=True)
    return signals
