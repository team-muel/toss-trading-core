"""Research performance metrics (research-only).

This module gives the repository the evaluation vocabulary it currently lacks:
Sharpe, returns, turnover, drawdown and the composite *fitness* score used to
rank alphas, plus a walk-forward in-sample / out-of-sample split so signals are
judged on held-out data rather than the window they were fit on.

Everything operates on plain Python sequences and panels
(``dict[str, list[float]]``) so no numeric third-party dependency is required.
None of these functions touch the broker; they score simulated PnL only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import sqrt

TRADING_DAYS = 252
TURNOVER_FLOOR = 0.125  # BRAIN fitness floor: never reward sub-0.125 turnover further


Panel = Mapping[str, Sequence[float]]


def daily_pnl(positions: Panel, forward_returns: Panel) -> list[float]:
    """Book PnL per period: ``sum_i w[i, t] * r[i, t]``.

    ``positions[sym][t]`` is the weight held into period ``t`` and
    ``forward_returns[sym][t]`` is that symbol's realised return over period
    ``t``.  Both panels must share the same length per symbol.
    """
    length = _panel_length(positions)
    if length != _panel_length(forward_returns):
        raise ValueError("positions and forward_returns must have equal length")
    pnl: list[float] = []
    for t in range(length):
        total = 0.0
        for symbol, weights in positions.items():
            ret = forward_returns.get(symbol)
            if ret is None:
                continue
            weight = weights[t]
            value = ret[t]
            if weight is None or value is None:
                continue
            total += float(weight) * float(value)
        pnl.append(total)
    return pnl


def sharpe(pnl: Sequence[float], periods: int = TRADING_DAYS) -> float:
    series = [float(value) for value in pnl if value is not None]
    if len(series) < 2:
        return 0.0
    mean = sum(series) / len(series)
    variance = sum((value - mean) ** 2 for value in series) / (len(series) - 1)
    std = sqrt(variance)
    if std == 0:
        return 0.0
    return sqrt(periods) * mean / std


def annualized_returns(
    pnl: Sequence[float], book_size: float, periods: int = TRADING_DAYS
) -> float:
    """Annualised return on book: ``mean(daily_pnl) / book * periods``."""
    series = [float(value) for value in pnl if value is not None]
    if not series or book_size <= 0:
        return 0.0
    return (sum(series) / len(series)) / book_size * periods


def turnover(positions: Panel) -> float:
    """Average fraction of the book traded per period.

    ``mean_t ( sum_i |w[i, t] - w[i, t-1]| ) / mean gross book``.  Higher
    turnover means higher transaction cost, exactly as on BRAIN.
    """
    length = _panel_length(positions)
    if length < 2:
        return 0.0
    traded: list[float] = []
    gross: list[float] = []
    for t in range(length):
        step_traded = 0.0
        step_gross = 0.0
        for weights in positions.values():
            current = weights[t] or 0.0
            prior = weights[t - 1] or 0.0 if t > 0 else 0.0
            step_traded += abs(float(current) - float(prior))
            step_gross += abs(float(current))
        if t > 0:
            traded.append(step_traded)
        gross.append(step_gross)
    mean_gross = sum(gross) / len(gross)
    if mean_gross == 0:
        return 0.0
    return (sum(traded) / len(traded)) / mean_gross


def max_drawdown(pnl: Sequence[float]) -> float:
    """Largest peak-to-trough drop of the cumulative PnL curve (>= 0)."""
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for value in pnl:
        if value is None:
            continue
        cumulative += float(value)
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return -worst


def fitness(sharpe_value: float, returns: float, turnover_value: float) -> float:
    """WorldQuant BRAIN fitness score.

    ``fitness = sharpe * sqrt(|returns| / max(turnover, 0.125))``.
    """
    denom = max(turnover_value, TURNOVER_FLOOR)
    return sharpe_value * sqrt(abs(returns) / denom)


def information_ratio(pnl: Sequence[float]) -> float:
    """Non-annualised mean/std of PnL (per-period information ratio)."""
    series = [float(value) for value in pnl if value is not None]
    if len(series) < 2:
        return 0.0
    mean = sum(series) / len(series)
    variance = sum((value - mean) ** 2 for value in series) / (len(series) - 1)
    std = sqrt(variance)
    return 0.0 if std == 0 else mean / std


def is_os_split(length: int, os_fraction: float = 0.25) -> tuple[range, range]:
    """Walk-forward split: earliest ``1 - os_fraction`` is in-sample.

    Returns ``(in_sample_range, out_of_sample_range)`` index ranges so alphas
    are only accepted when the out-of-sample slice confirms the in-sample fit.
    """
    if not 0 < os_fraction < 1:
        raise ValueError("os_fraction must be in (0, 1)")
    if length < 2:
        raise ValueError("need at least two periods to split")
    cut = max(1, int(round(length * (1 - os_fraction))))
    cut = min(cut, length - 1)
    return range(0, cut), range(cut, length)


@dataclass(frozen=True)
class SimulationResult:
    """Bundle of the headline metrics for one simulated alpha."""

    sharpe: float
    returns: float
    turnover: float
    fitness: float
    max_drawdown: float
    periods: int

    def summary(self) -> str:
        return (
            f"sharpe={self.sharpe:.2f} returns={self.returns:.2%} "
            f"turnover={self.turnover:.2%} fitness={self.fitness:.2f} "
            f"max_drawdown={self.max_drawdown:.4f} periods={self.periods}"
        )


def evaluate(positions: Panel, forward_returns: Panel, book_size: float) -> SimulationResult:
    """Compute the full metric bundle for a simulated position panel."""
    pnl = daily_pnl(positions, forward_returns)
    sharpe_value = sharpe(pnl)
    returns = annualized_returns(pnl, book_size)
    turnover_value = turnover(positions)
    return SimulationResult(
        sharpe=sharpe_value,
        returns=returns,
        turnover=turnover_value,
        fitness=fitness(sharpe_value, returns, turnover_value),
        max_drawdown=max_drawdown(pnl),
        periods=len(pnl),
    )


def _panel_length(panel: Panel) -> int:
    lengths = {len(series) for series in panel.values()}
    if not lengths:
        return 0
    if len(lengths) != 1:
        raise ValueError("panel series must all share one length")
    return next(iter(lengths))
