"""Research performance metrics for alpha evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import sqrt

TRADING_DAYS = 252
TURNOVER_FLOOR = 0.125
Panel = Mapping[str, Sequence[float]]


def _panel_length(panel: Panel) -> int:
    lengths = {len(series) for series in panel.values()}
    if not lengths:
        return 0
    if len(lengths) != 1:
        raise ValueError("panel series must all share one length")
    return next(iter(lengths))


def daily_pnl(positions: Panel, forward_returns: Panel) -> list[float]:
    length = _panel_length(positions)
    if length != _panel_length(forward_returns):
        raise ValueError("positions and forward_returns must have equal length")
    pnl: list[float] = []
    for t in range(length):
        total = 0.0
        for symbol, weights in positions.items():
            returns = forward_returns.get(symbol)
            if returns is None:
                continue
            weight = weights[t]
            realised = returns[t]
            if weight is None or realised is None:
                continue
            total += float(weight) * float(realised)
        pnl.append(total)
    return pnl


def sharpe(pnl: Sequence[float], periods: int = TRADING_DAYS) -> float:
    values = [float(value) for value in pnl if value is not None]
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    std = sqrt(variance)
    return 0.0 if std == 0 else sqrt(periods) * mean / std


def annualized_returns(pnl: Sequence[float], book_size: float, periods: int = TRADING_DAYS) -> float:
    values = [float(value) for value in pnl if value is not None]
    if not values or book_size <= 0:
        return 0.0
    return (sum(values) / len(values)) / book_size * periods


def turnover(positions: Panel) -> float:
    length = _panel_length(positions)
    if length < 2:
        return 0.0
    traded: list[float] = []
    gross: list[float] = []
    for t in range(length):
        step_traded = 0.0
        step_gross = 0.0
        for weights in positions.values():
            current = float(weights[t] or 0.0)
            prior = float(weights[t - 1] or 0.0) if t > 0 else 0.0
            step_traded += abs(current - prior)
            step_gross += abs(current)
        if t > 0:
            traded.append(step_traded)
        gross.append(step_gross)
    mean_gross = sum(gross) / len(gross)
    return 0.0 if mean_gross == 0 else (sum(traded) / len(traded)) / mean_gross


def max_drawdown(pnl: Sequence[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for value in pnl:
        cumulative += float(value)
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return -worst


def fitness(sharpe_value: float, returns: float, turnover_value: float) -> float:
    """WorldQuant BRAIN-style fitness score."""

    return sharpe_value * sqrt(abs(returns) / max(turnover_value, TURNOVER_FLOOR))


def is_os_split(length: int, os_fraction: float = 0.25) -> tuple[range, range]:
    if not 0 < os_fraction < 1:
        raise ValueError("os_fraction must be in (0, 1)")
    if length < 2:
        raise ValueError("need at least two periods to split")
    cut = max(1, int(round(length * (1 - os_fraction))))
    cut = min(cut, length - 1)
    return range(0, cut), range(cut, length)


@dataclass(frozen=True)
class SimulationResult:
    sharpe: float
    returns: float
    turnover: float
    fitness: float
    max_drawdown: float
    periods: int


def evaluate(positions: Panel, forward_returns: Panel, book_size: float) -> SimulationResult:
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
