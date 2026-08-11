from __future__ import annotations

import hashlib
import json
import math
import statistics
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from toss_trading.research.prospective import assess_collection_continuity
from toss_trading.research.costs import ExecutionCostModel
from toss_trading.research.macro import (
    MACRO_SIGNAL_NAMES,
    MacroVintageObservation,
    PointInTimeMacroStore,
)


STRATEGY_IMPLEMENTATION_VERSION = 3


@dataclass(frozen=True)
class PricePoint:
    date: str
    symbol: str
    total_return_index: str
    available_at: str


@dataclass(frozen=True)
class DualMomentumConfig:
    candidate_symbols: tuple[str, ...]
    cash_symbol: str = "SGOV"
    lookback_trading_days: int = 252
    skip_recent_trading_days: int = 21
    top_k: int = 1
    minimum_absolute_momentum: float = 0.0
    walk_forward_train_days: int = 504
    walk_forward_test_days: int = 126

    def validate(self) -> None:
        if not self.candidate_symbols:
            raise ValueError("candidate_symbols must not be empty")
        if self.cash_symbol in self.candidate_symbols:
            raise ValueError("cash_symbol must not be a momentum candidate")
        if self.lookback_trading_days <= self.skip_recent_trading_days:
            raise ValueError("lookback must exceed the recent skip window")
        if self.skip_recent_trading_days < 0:
            raise ValueError("skip_recent_trading_days must be nonnegative")
        if not 1 <= self.top_k <= len(self.candidate_symbols):
            raise ValueError("top_k is outside the candidate universe")
        if min(self.walk_forward_train_days, self.walk_forward_test_days) <= 0:
            raise ValueError("walk-forward train and test windows must be positive")


QUANT_FACTOR_NAMES = (
    "momentum",
    "risk_adjusted_momentum",
    "short_term_reversal",
    "low_volatility",
    "trend_acceleration",
)


@dataclass(frozen=True)
class QuantFactorConfig:
    """Bounded, code-free DSL for cross-sectional ETF factor research."""

    candidate_symbols: tuple[str, ...]
    cash_symbol: str
    factor_weights: tuple[tuple[str, float], ...]
    long_lookback_trading_days: int
    short_lookback_trading_days: int
    volatility_window_trading_days: int
    skip_recent_trading_days: int
    top_k: int
    weighting: str
    rebalance_frequency: str
    regime_filter: str
    minimum_composite_score: float
    walk_forward_train_days: int
    walk_forward_test_days: int

    @property
    def factors(self) -> dict[str, float]:
        return dict(self.factor_weights)

    def validate(self) -> None:
        if len(self.candidate_symbols) < 2:
            raise ValueError("candidate_symbols needs at least two symbols")
        if self.cash_symbol in self.candidate_symbols:
            raise ValueError("cash_symbol must not be a factor candidate")
        weights = self.factors
        if set(weights) != set(QUANT_FACTOR_NAMES):
            raise ValueError("factor_weights differs from the bounded factor set")
        if any(value < 0 or value > 1 for value in weights.values()):
            raise ValueError("factor weights must be between zero and one")
        if not any(value > 0 for value in weights.values()):
            raise ValueError("at least one factor must be active")
        if self.long_lookback_trading_days <= self.short_lookback_trading_days:
            raise ValueError("long lookback must exceed short lookback")
        if min(
            self.short_lookback_trading_days,
            self.volatility_window_trading_days,
        ) < 2:
            raise ValueError("factor windows are too short")
        if self.skip_recent_trading_days < 0:
            raise ValueError("skip_recent_trading_days must be nonnegative")
        if not 1 <= self.top_k <= len(self.candidate_symbols):
            raise ValueError("top_k is outside the candidate universe")
        if self.weighting not in {"equal", "inverse_volatility"}:
            raise ValueError("unsupported factor portfolio weighting")
        if self.rebalance_frequency not in {"weekly", "monthly"}:
            raise ValueError("unsupported factor rebalance frequency")
        if self.regime_filter not in {"none", "spy_absolute_momentum"}:
            raise ValueError("unsupported market regime filter")
        if not -1 <= self.minimum_composite_score <= 1:
            raise ValueError("minimum_composite_score must be between -1 and one")
        if min(self.walk_forward_train_days, self.walk_forward_test_days) <= 0:
            raise ValueError("walk-forward train and test windows must be positive")


@dataclass(frozen=True)
class MacroRegimeConfig:
    """Bounded allocation rules driven only by point-in-time macro vintages."""

    risk_on_symbols: tuple[str, ...]
    defensive_symbols: tuple[str, ...]
    cash_symbol: str
    macro_signal_weights: tuple[tuple[str, float], ...]
    signal_lookback_months: int
    minimum_regime_score: float
    rebalance_frequency: str
    publication_lag_days: int
    walk_forward_train_days: int
    walk_forward_test_days: int

    @property
    def candidate_symbols(self) -> tuple[str, ...]:
        return self.risk_on_symbols

    @property
    def signals(self) -> dict[str, float]:
        return dict(self.macro_signal_weights)

    def validate(self) -> None:
        if len(self.risk_on_symbols) < 2 or len(set(self.risk_on_symbols)) != len(
            self.risk_on_symbols
        ):
            raise ValueError("macro risk-on universe must contain unique symbols")
        if not self.defensive_symbols or len(set(self.defensive_symbols)) != len(
            self.defensive_symbols
        ):
            raise ValueError("macro defensive universe must contain unique symbols")
        if set(self.risk_on_symbols) & set(self.defensive_symbols):
            raise ValueError("macro risk-on and defensive universes must not overlap")
        if self.cash_symbol not in self.defensive_symbols:
            raise ValueError("macro defensive universe must include cash_symbol")
        signals = self.signals
        if set(signals) != set(MACRO_SIGNAL_NAMES):
            raise ValueError("macro signal weights differ from the bounded signal set")
        if any(value < 0 or value > 1 for value in signals.values()):
            raise ValueError("macro signal weights must be between zero and one")
        if not any(value > 0 for value in signals.values()):
            raise ValueError("at least one macro signal must be active")
        if self.signal_lookback_months not in {3, 6, 12}:
            raise ValueError("unsupported macro signal lookback")
        if not -1 <= self.minimum_regime_score <= 1:
            raise ValueError("macro regime threshold must be between minus and plus one")
        if self.rebalance_frequency != "monthly":
            raise ValueError("macro regime research must rebalance monthly")
        if not 1 <= self.publication_lag_days <= 7:
            raise ValueError("macro publication lag must be between one and seven days")
        if min(self.walk_forward_train_days, self.walk_forward_test_days) <= 0:
            raise ValueError("walk-forward train and test windows must be positive")


@dataclass(frozen=True)
class Rebalance:
    signal_date: str
    effective_date: str
    scores: dict[str, float]
    target_weights: dict[str, float]
    turnover: float
    cost_fraction: float
    gross_order_notional_usd: float = 0.0
    commission_cost_fraction: float = 0.0
    slippage_cost_fraction: float = 0.0


@dataclass(frozen=True)
class BacktestResult:
    config: DualMomentumConfig | QuantFactorConfig | MacroRegimeConfig
    equity_curve: tuple[tuple[str, float], ...]
    daily_returns: tuple[tuple[str, float], ...]
    rebalances: tuple[Rebalance, ...]
    metrics: dict[str, float]
    benchmark_metrics: dict[str, dict[str, float]]
    benchmark_daily_returns: dict[str, tuple[tuple[str, float], ...]]
    walk_forward_folds: tuple["WalkForwardFold", ...]
    execution_cost_model: ExecutionCostModel


@dataclass(frozen=True)
class WalkForwardFold:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    metrics: dict[str, float]
    benchmark_name: str
    benchmark_metrics: dict[str, float]
    excess_metrics: dict[str, float]
    passed_relative_return: bool


def _number(value: str) -> float:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid total-return index: {value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"total-return index must be finite and positive: {value!r}")
    return float(result)


def _available_on_or_before(value: str, decision_date: str) -> bool:
    try:
        available = datetime.fromisoformat(value.replace("Z", "+00:00"))
        decision = date.fromisoformat(decision_date)
    except ValueError as exc:
        raise ValueError(f"invalid point-in-time timestamp: {value!r}") from exc
    if available.tzinfo is None or available.utcoffset() is None:
        raise ValueError(f"available_at must include a timezone: {value!r}")
    return available.astimezone(timezone.utc).date() <= decision


def _is_month_end(dates: list[str], index: int) -> bool:
    return index == len(dates) - 1 or dates[index][:7] != dates[index + 1][:7]


def _turnover(previous: dict[str, float], target: dict[str, float]) -> float:
    symbols = set(previous) | set(target)
    return 0.5 * sum(abs(target.get(symbol, 0.0) - previous.get(symbol, 0.0)) for symbol in symbols)


def _metrics(returns: list[float], equity: list[float], turnover: float) -> dict[str, float]:
    if not returns or not equity:
        raise ValueError("backtest produced no returns")
    years = len(returns) / 252.0
    total_return = equity[-1] - 1.0
    cagr = equity[-1] ** (1.0 / years) - 1.0 if years > 0 else 0.0
    volatility = (
        statistics.stdev(returns) * math.sqrt(252.0)
        if len(returns) > 1
        else 0.0
    )
    mean_daily = statistics.fmean(returns)
    sharpe = (
        mean_daily / statistics.stdev(returns) * math.sqrt(252.0)
        if len(returns) > 1 and statistics.stdev(returns) > 0
        else 0.0
    )
    peak = equity[0]
    max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1.0)
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0
    return {
        "total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": volatility,
        "sharpe_zero_rate": sharpe,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "turnover": turnover,
        "trading_days": float(len(returns)),
    }


def _static_portfolio_daily_returns(
    panel: dict[str, dict[str, float]],
    dates: list[str],
    weights: dict[str, float],
) -> tuple[tuple[str, float], ...]:
    returns: list[float] = []
    for index in range(1, len(dates)):
        previous_date = dates[index - 1]
        current_date = dates[index]
        daily_return = sum(
            weight
            * (panel[current_date][symbol] / panel[previous_date][symbol] - 1.0)
            for symbol, weight in weights.items()
        )
        returns.append((current_date, daily_return))
    return tuple(returns)


def _metrics_from_daily_returns(
    daily_returns: Iterable[tuple[str, float]],
    *,
    turnover: float = 0.0,
) -> dict[str, float]:
    values = [value for _, value in daily_returns]
    equity = 1.0
    equity_values = [equity]
    for value in values:
        equity *= 1.0 + value
        equity_values.append(equity)
    return _metrics(values, equity_values, turnover)


def _benchmark_daily_returns(
    panel: dict[str, dict[str, float]],
    dates: list[str],
    config: DualMomentumConfig,
) -> dict[str, tuple[tuple[str, float], ...]]:
    portfolios = {
        "equal-weight candidates": {
            symbol: 1.0 / len(config.candidate_symbols)
            for symbol in config.candidate_symbols
        },
        "cash": {config.cash_symbol: 1.0},
    }
    if "SPY" in panel[dates[0]]:
        portfolios["SPY buy-and-hold"] = {"SPY": 1.0}
    if {"SPY", "TLT"}.issubset(panel[dates[0]]):
        portfolios["60/40"] = {"SPY": 0.6, "TLT": 0.4}
    return {
        name: _static_portfolio_daily_returns(panel, dates, weights)
        for name, weights in sorted(portfolios.items())
    }


def _walk_forward_folds(
    daily_returns: list[tuple[str, float]],
    rebalances: list[Rebalance],
    benchmark_daily_returns: tuple[tuple[str, float], ...],
    *,
    benchmark_name: str,
    train_days: int,
    test_days: int,
) -> tuple[WalkForwardFold, ...]:
    """Evaluate sequential, non-overlapping OOS windows.

    The strategy is pre-registered and has no fitted coefficients, so the
    training window is evidence available before each fixed test window rather
    than a parameter-search interval.
    """

    folds: list[WalkForwardFold] = []
    benchmark_by_date = dict(benchmark_daily_returns)
    test_start = train_days
    while test_start + test_days <= len(daily_returns):
        train_start = test_start - train_days
        test_end = test_start + test_days
        test_slice = daily_returns[test_start:test_end]
        test_dates = {item[0] for item in test_slice}
        test_values = [item[1] for item in test_slice]
        benchmark_slice = [
            (item_date, benchmark_by_date[item_date])
            for item_date, _ in test_slice
            if item_date in benchmark_by_date
        ]
        if len(benchmark_slice) != len(test_slice):
            raise ValueError("walk-forward benchmark is not date-aligned")
        paired_excess = [
            value - benchmark_by_date[item_date]
            for item_date, value in test_slice
        ]
        equity = 1.0
        equity_values = [equity]
        for value in test_values:
            equity *= 1.0 + value
            equity_values.append(equity)
        turnover = sum(
            item.turnover
            for item in rebalances
            if item.effective_date in test_dates
        )
        strategy_metrics = _metrics(test_values, equity_values, turnover)
        benchmark_metrics = _metrics_from_daily_returns(benchmark_slice)
        excess_total_return = (
            strategy_metrics["total_return"] - benchmark_metrics["total_return"]
        )
        excess_volatility = (
            statistics.stdev(paired_excess) * math.sqrt(252.0)
            if len(paired_excess) > 1
            else 0.0
        )
        annualized_mean_excess = statistics.fmean(paired_excess) * 252.0
        folds.append(
            WalkForwardFold(
                train_start=daily_returns[train_start][0],
                train_end=daily_returns[test_start - 1][0],
                test_start=test_slice[0][0],
                test_end=test_slice[-1][0],
                metrics=strategy_metrics,
                benchmark_name=benchmark_name,
                benchmark_metrics=benchmark_metrics,
                excess_metrics={
                    "total_return": excess_total_return,
                    "annualized_mean_excess": annualized_mean_excess,
                    "tracking_error": excess_volatility,
                    "information_ratio": (
                        annualized_mean_excess / excess_volatility
                        if excess_volatility > 0
                        else 0.0
                    ),
                },
                passed_relative_return=excess_total_return > 0.0,
            )
        )
        test_start = test_end
    return tuple(folds)


def run_dual_momentum_backtest(
    points: Iterable[PricePoint],
    config: DualMomentumConfig,
    *,
    execution_cost_model: ExecutionCostModel,
) -> BacktestResult:
    """Run a monthly 12-1-style total-return momentum baseline with next-day execution."""

    config.validate()
    cost_model = execution_cost_model
    cost_model.validate()
    panel: dict[str, dict[str, float]] = {}
    availability: dict[tuple[str, str], str] = {}
    for point in points:
        key = (point.date, point.symbol)
        if key in availability:
            raise ValueError(f"duplicate price point: {key}")
        panel.setdefault(point.date, {})[point.symbol] = _number(point.total_return_index)
        availability[key] = point.available_at
    dates = sorted(panel)
    required = set(config.candidate_symbols) | {config.cash_symbol}
    if len(dates) <= config.lookback_trading_days + 1:
        raise ValueError("insufficient history for configured momentum lookback")
    for date in dates:
        missing = required - set(panel[date])
        if missing:
            raise ValueError(f"missing symbols on {date}: {sorted(missing)}")

    weights = {config.cash_symbol: 1.0}
    pending: tuple[str, dict[str, float], dict[str, float]] | None = None
    equity = 1.0
    equity_curve: list[tuple[str, float]] = [(dates[0], equity)]
    daily_returns: list[tuple[str, float]] = []
    rebalances: list[Rebalance] = []
    total_turnover = 0.0

    for index in range(1, len(dates)):
        current_date = dates[index]
        transaction_cost = 0.0
        previous_date = dates[index - 1]
        gross_return = sum(
            weight
            * (
                panel[current_date][symbol] / panel[previous_date][symbol]
                - 1.0
            )
            for symbol, weight in weights.items()
        )
        if pending is not None:
            signal_date, scores, target = pending
            turnover = _turnover(weights, target)
            cost_estimate = cost_model.estimate_rebalance(
                weights,
                target,
                equity_multiple=equity,
            )
            transaction_cost = cost_estimate["total_fraction"]
            total_turnover += turnover
            weights = target
            rebalances.append(
                Rebalance(
                    signal_date=signal_date,
                    effective_date=current_date,
                    scores=scores,
                    target_weights=target,
                    turnover=turnover,
                    cost_fraction=transaction_cost,
                    gross_order_notional_usd=cost_estimate[
                        "gross_order_notional_usd"
                    ],
                    commission_cost_fraction=cost_estimate[
                        "commission_fraction"
                    ],
                    slippage_cost_fraction=cost_estimate["slippage_fraction"],
                )
            )
            pending = None
        net_return = gross_return - transaction_cost
        equity *= 1.0 + net_return
        if equity <= 0:
            raise ValueError("strategy equity became nonpositive")
        daily_returns.append((current_date, net_return))
        equity_curve.append((current_date, equity))

        if (
            _is_month_end(dates, index)
            and index >= config.lookback_trading_days
            and index + 1 < len(dates)
        ):
            recent_index = index - config.skip_recent_trading_days
            old_index = index - config.lookback_trading_days
            recent_date = dates[recent_index]
            old_date = dates[old_index]
            scores: dict[str, float] = {}
            for symbol in config.candidate_symbols:
                if not all(
                    _available_on_or_before(availability[(price_date, symbol)], current_date)
                    for price_date in (old_date, recent_date)
                ):
                    raise ValueError(
                        f"point-in-time violation for {symbol} on {current_date}"
                    )
                scores[symbol] = (
                    panel[recent_date][symbol] / panel[old_date][symbol] - 1.0
                )
            selected = [
                symbol
                for symbol, score in sorted(
                    scores.items(),
                    key=lambda item: (-item[1], item[0]),
                )
                if score > config.minimum_absolute_momentum
            ][: config.top_k]
            if selected:
                target = {symbol: 1.0 / len(selected) for symbol in selected}
            else:
                target = {config.cash_symbol: 1.0}
            pending = (current_date, scores, target)

    benchmark_daily_returns = _benchmark_daily_returns(panel, dates, config)
    primary_benchmark = (
        "SPY buy-and-hold"
        if "SPY buy-and-hold" in benchmark_daily_returns
        else "equal-weight candidates"
    )
    folds = _walk_forward_folds(
        daily_returns,
        rebalances,
        benchmark_daily_returns[primary_benchmark],
        benchmark_name=primary_benchmark,
        train_days=config.walk_forward_train_days,
        test_days=config.walk_forward_test_days,
    )
    return BacktestResult(
        config=config,
        equity_curve=tuple(equity_curve),
        daily_returns=tuple(daily_returns),
        rebalances=tuple(rebalances),
        metrics=_metrics(
            [value for _, value in daily_returns],
            [value for _, value in equity_curve],
            total_turnover,
        ),
        benchmark_metrics={
            name: _metrics_from_daily_returns(values)
            for name, values in benchmark_daily_returns.items()
        },
        benchmark_daily_returns=benchmark_daily_returns,
        walk_forward_folds=folds,
        execution_cost_model=cost_model,
    )


def _is_week_end(dates: list[str], index: int) -> bool:
    if index == len(dates) - 1:
        return True
    return date.fromisoformat(dates[index]).isocalendar()[:2] != date.fromisoformat(
        dates[index + 1]
    ).isocalendar()[:2]


def _rank_factor(values: dict[str, float]) -> dict[str, float]:
    """Map a cross-section to deterministic scores in [-1, 1]."""

    ordered = sorted(values, key=lambda symbol: (values[symbol], symbol))
    if len(ordered) == 1:
        return {ordered[0]: 0.0}
    return {
        symbol: -1.0 + 2.0 * index / (len(ordered) - 1)
        for index, symbol in enumerate(ordered)
    }


def run_quant_factor_backtest(
    points: Iterable[PricePoint],
    config: QuantFactorConfig,
    *,
    execution_cost_model: ExecutionCostModel,
) -> BacktestResult:
    """Evaluate bounded factor combinations with delayed, costed execution.

    The model may choose a pre-approved combination of factors and portfolio
    rules, but it cannot provide executable code or arbitrary expressions.
    Every signal uses point-in-time total-return observations and is applied on
    the following trading day.
    """

    config.validate()
    execution_cost_model.validate()
    panel: dict[str, dict[str, float]] = {}
    availability: dict[tuple[str, str], str] = {}
    for point in points:
        key = (point.date, point.symbol)
        if key in availability:
            raise ValueError(f"duplicate price point: {key}")
        panel.setdefault(point.date, {})[point.symbol] = _number(
            point.total_return_index
        )
        availability[key] = point.available_at
    dates = sorted(panel)
    required = set(config.candidate_symbols) | {config.cash_symbol, "SPY"}
    warmup = config.skip_recent_trading_days + max(
        config.long_lookback_trading_days,
        config.volatility_window_trading_days,
    )
    if len(dates) <= warmup + 1:
        raise ValueError("insufficient history for configured factor windows")
    for item_date in dates:
        missing = required - set(panel[item_date])
        if missing:
            raise ValueError(f"missing symbols on {item_date}: {sorted(missing)}")

    weights = {config.cash_symbol: 1.0}
    pending: tuple[str, dict[str, float], dict[str, float]] | None = None
    equity = 1.0
    equity_curve: list[tuple[str, float]] = [(dates[0], equity)]
    daily_returns: list[tuple[str, float]] = []
    rebalances: list[Rebalance] = []
    total_turnover = 0.0
    factor_weights = config.factors

    for index in range(1, len(dates)):
        current_date = dates[index]
        previous_date = dates[index - 1]
        gross_return = sum(
            weight
            * (panel[current_date][symbol] / panel[previous_date][symbol] - 1.0)
            for symbol, weight in weights.items()
        )
        transaction_cost = 0.0
        if pending is not None:
            signal_date, scores, target = pending
            turnover = _turnover(weights, target)
            estimate = execution_cost_model.estimate_rebalance(
                weights,
                target,
                equity_multiple=equity,
            )
            transaction_cost = estimate["total_fraction"]
            total_turnover += turnover
            weights = target
            rebalances.append(
                Rebalance(
                    signal_date=signal_date,
                    effective_date=current_date,
                    scores=scores,
                    target_weights=target,
                    turnover=turnover,
                    cost_fraction=transaction_cost,
                    gross_order_notional_usd=estimate["gross_order_notional_usd"],
                    commission_cost_fraction=estimate["commission_fraction"],
                    slippage_cost_fraction=estimate["slippage_fraction"],
                )
            )
            pending = None
        net_return = gross_return - transaction_cost
        equity *= 1.0 + net_return
        if equity <= 0:
            raise ValueError("strategy equity became nonpositive")
        daily_returns.append((current_date, net_return))
        equity_curve.append((current_date, equity))

        is_rebalance = (
            _is_month_end(dates, index)
            if config.rebalance_frequency == "monthly"
            else _is_week_end(dates, index)
        )
        if not is_rebalance or index < warmup or index + 1 >= len(dates):
            continue

        recent_index = index - config.skip_recent_trading_days
        long_index = recent_index - config.long_lookback_trading_days
        short_index = recent_index - config.short_lookback_trading_days
        volatility_index = recent_index - config.volatility_window_trading_days
        checked_dates = {
            dates[recent_index],
            dates[long_index],
            dates[short_index],
            dates[volatility_index],
        }
        for symbol in required:
            if not all(
                _available_on_or_before(availability[(item_date, symbol)], current_date)
                for item_date in checked_dates
            ):
                raise ValueError(
                    f"point-in-time violation for {symbol} on {current_date}"
                )

        raw: dict[str, dict[str, float]] = {
            factor: {} for factor in QUANT_FACTOR_NAMES
        }
        volatilities: dict[str, float] = {}
        for symbol in config.candidate_symbols:
            long_return = (
                panel[dates[recent_index]][symbol]
                / panel[dates[long_index]][symbol]
                - 1.0
            )
            short_return = (
                panel[dates[recent_index]][symbol]
                / panel[dates[short_index]][symbol]
                - 1.0
            )
            window_returns = [
                panel[dates[position]][symbol]
                / panel[dates[position - 1]][symbol]
                - 1.0
                for position in range(volatility_index + 1, recent_index + 1)
            ]
            volatility = (
                statistics.stdev(window_returns) * math.sqrt(252.0)
                if len(window_returns) > 1
                else 0.0
            )
            volatilities[symbol] = volatility
            raw["momentum"][symbol] = long_return
            raw["risk_adjusted_momentum"][symbol] = (
                long_return / volatility if volatility > 0 else 0.0
            )
            raw["short_term_reversal"][symbol] = -short_return
            raw["low_volatility"][symbol] = -volatility
            raw["trend_acceleration"][symbol] = (
                short_return / config.short_lookback_trading_days
                - long_return / config.long_lookback_trading_days
            )

        ranked = {factor: _rank_factor(values) for factor, values in raw.items()}
        normalization = sum(factor_weights.values())
        scores = {
            symbol: sum(
                factor_weights[factor] * ranked[factor][symbol]
                for factor in QUANT_FACTOR_NAMES
            )
            / normalization
            for symbol in config.candidate_symbols
        }
        regime_passed = True
        if config.regime_filter == "spy_absolute_momentum":
            regime_passed = (
                panel[dates[recent_index]]["SPY"]
                / panel[dates[long_index]]["SPY"]
                - 1.0
            ) > 0.0
        selected = (
            [
                symbol
                for symbol in sorted(
                    config.candidate_symbols,
                    key=lambda item: (-scores[item], item),
                )
                if scores[symbol] >= config.minimum_composite_score
            ][: config.top_k]
            if regime_passed
            else []
        )
        if not selected:
            target = {config.cash_symbol: 1.0}
        elif config.weighting == "equal":
            target = {symbol: 1.0 / len(selected) for symbol in selected}
        else:
            inverse = {
                symbol: 1.0 / max(volatilities[symbol], 1e-8)
                for symbol in selected
            }
            denominator = sum(inverse.values())
            target = {
                symbol: inverse[symbol] / denominator for symbol in selected
            }
        pending = (current_date, scores, target)

    benchmark_daily_returns = _benchmark_daily_returns(panel, dates, config)
    primary_benchmark = "SPY buy-and-hold"
    folds = _walk_forward_folds(
        daily_returns,
        rebalances,
        benchmark_daily_returns[primary_benchmark],
        benchmark_name=primary_benchmark,
        train_days=config.walk_forward_train_days,
        test_days=config.walk_forward_test_days,
    )
    return BacktestResult(
        config=config,
        equity_curve=tuple(equity_curve),
        daily_returns=tuple(daily_returns),
        rebalances=tuple(rebalances),
        metrics=_metrics(
            [value for _, value in daily_returns],
            [value for _, value in equity_curve],
            total_turnover,
        ),
        benchmark_metrics={
            name: _metrics_from_daily_returns(values)
            for name, values in benchmark_daily_returns.items()
        },
        benchmark_daily_returns=benchmark_daily_returns,
        walk_forward_folds=folds,
        execution_cost_model=execution_cost_model,
    )


def run_macro_regime_backtest(
    points: Iterable[PricePoint],
    macro_observations: Iterable[MacroVintageObservation],
    config: MacroRegimeConfig,
    *,
    execution_cost_model: ExecutionCostModel,
) -> BacktestResult:
    """Allocate with ALFRED vintages known at each historical decision date."""

    config.validate()
    execution_cost_model.validate()
    macro = PointInTimeMacroStore(
        macro_observations,
        publication_lag_days=config.publication_lag_days,
    )
    panel: dict[str, dict[str, float]] = {}
    availability: dict[tuple[str, str], str] = {}
    for point in points:
        key = (point.date, point.symbol)
        if key in availability:
            raise ValueError(f"duplicate price point: {key}")
        panel.setdefault(point.date, {})[point.symbol] = _number(
            point.total_return_index
        )
        availability[key] = point.available_at
    dates = sorted(panel)
    required = (
        set(config.risk_on_symbols)
        | set(config.defensive_symbols)
        | {"SPY"}
    )
    if len(dates) <= config.walk_forward_train_days + 1:
        raise ValueError("insufficient price history for macro regime research")
    for item_date in dates:
        missing = required - set(panel[item_date])
        if missing:
            raise ValueError(f"missing symbols on {item_date}: {sorted(missing)}")

    weights = {config.cash_symbol: 1.0}
    pending: tuple[str, dict[str, float], dict[str, float]] | None = None
    equity = 1.0
    equity_curve: list[tuple[str, float]] = [(dates[0], equity)]
    daily_returns: list[tuple[str, float]] = []
    rebalances: list[Rebalance] = []
    total_turnover = 0.0
    signal_weights = config.signals
    normalization = sum(signal_weights.values())

    for index in range(1, len(dates)):
        current_date = dates[index]
        previous_date = dates[index - 1]
        gross_return = sum(
            weight
            * (panel[current_date][symbol] / panel[previous_date][symbol] - 1.0)
            for symbol, weight in weights.items()
        )
        transaction_cost = 0.0
        if pending is not None:
            signal_date, scores, target = pending
            turnover = _turnover(weights, target)
            estimate = execution_cost_model.estimate_rebalance(
                weights,
                target,
                equity_multiple=equity,
            )
            transaction_cost = estimate["total_fraction"]
            total_turnover += turnover
            weights = target
            rebalances.append(
                Rebalance(
                    signal_date=signal_date,
                    effective_date=current_date,
                    scores=scores,
                    target_weights=target,
                    turnover=turnover,
                    cost_fraction=transaction_cost,
                    gross_order_notional_usd=estimate["gross_order_notional_usd"],
                    commission_cost_fraction=estimate["commission_fraction"],
                    slippage_cost_fraction=estimate["slippage_fraction"],
                )
            )
            pending = None
        net_return = gross_return - transaction_cost
        equity *= 1.0 + net_return
        if equity <= 0:
            raise ValueError("strategy equity became nonpositive")
        daily_returns.append((current_date, net_return))
        equity_curve.append((current_date, equity))

        if not _is_month_end(dates, index) or index + 1 >= len(dates):
            continue
        if not all(
            _available_on_or_before(availability[(current_date, symbol)], current_date)
            for symbol in required
        ):
            raise ValueError(f"point-in-time price violation on {current_date}")
        signals = macro.regime_signals(
            current_date,
            lookback_months=config.signal_lookback_months,
        )
        if signals is None:
            continue
        regime_score = sum(
            signal_weights[name] * signals[name] for name in MACRO_SIGNAL_NAMES
        ) / normalization
        selected = (
            config.risk_on_symbols
            if regime_score >= config.minimum_regime_score
            else config.defensive_symbols
        )
        target = {symbol: 1.0 / len(selected) for symbol in selected}
        pending = (
            current_date,
            {**signals, "macro_regime_score": regime_score},
            target,
        )

    if not rebalances:
        raise ValueError("macro regime research produced no eligible rebalances")
    benchmark_daily_returns = _benchmark_daily_returns(panel, dates, config)
    primary_benchmark = "SPY buy-and-hold"
    folds = _walk_forward_folds(
        daily_returns,
        rebalances,
        benchmark_daily_returns[primary_benchmark],
        benchmark_name=primary_benchmark,
        train_days=config.walk_forward_train_days,
        test_days=config.walk_forward_test_days,
    )
    return BacktestResult(
        config=config,
        equity_curve=tuple(equity_curve),
        daily_returns=tuple(daily_returns),
        rebalances=tuple(rebalances),
        metrics=_metrics(
            [value for _, value in daily_returns],
            [value for _, value in equity_curve],
            total_turnover,
        ),
        benchmark_metrics={
            name: _metrics_from_daily_returns(values)
            for name, values in benchmark_daily_returns.items()
        },
        benchmark_daily_returns=benchmark_daily_returns,
        walk_forward_folds=folds,
        execution_cost_model=execution_cost_model,
    )


def write_experiment_record(
    result: BacktestResult,
    *,
    output_root: str | Path,
    data_manifest_ids: Iterable[str],
    code_revision: str,
    benchmark_names: Iterable[str],
    validation_protocol: dict[str, object] | None = None,
    prospective_observations: Iterable[dict[str, object]] = (),
) -> Path:
    """Persist a reproducible experiment record without mutating prior results."""

    revision = code_revision.strip()
    if not revision or revision.lower() == "unknown":
        raise ValueError("an immutable code revision is required")
    manifest_ids = sorted(
        {item.strip() for item in data_manifest_ids if item.strip()}
    )
    if not manifest_ids:
        raise ValueError("at least one data manifest id is required")
    benchmarks = sorted(
        {item.strip() for item in benchmark_names if item.strip()}
    )
    if not benchmarks:
        raise ValueError("at least one benchmark name is required")
    unavailable_benchmarks = sorted(set(benchmarks) - set(result.benchmark_metrics))
    if unavailable_benchmarks:
        raise ValueError(
            f"requested benchmark results are unavailable: {unavailable_benchmarks}"
        )
    selected_benchmark_metrics = {
        name: result.benchmark_metrics[name]
        for name in benchmarks
    }
    headline_metrics: dict[str, float] | None = result.metrics
    headline_benchmarks = selected_benchmark_metrics
    protocol_record: dict[str, object] = {
        "version": 1,
        "parameter_selection": "fixed_before_run",
        "walk_forward_role": "diagnostic_only",
        "untouched_holdout": False,
        "headline_metrics_scope": "full_sample",
    }
    prospective_holdout: dict[str, object] | None = None
    diagnostic_benchmark_metrics = selected_benchmark_metrics
    diagnostic_metrics = result.metrics
    published_rebalances = list(result.rebalances)
    published_folds = list(result.walk_forward_folds)
    published_equity_curve = list(result.equity_curve)
    diagnostic_sample_end = result.daily_returns[-1][0]
    if validation_protocol is not None:
        if validation_protocol.get("schema_version") != "research-validation-v2":
            raise ValueError("unsupported validation protocol schema")
        if validation_protocol.get("strategy") != "broad_etf_dual_momentum_v1":
            raise ValueError("validation protocol strategy mismatch")
        if (
            validation_protocol.get("implementation_version")
            != STRATEGY_IMPLEMENTATION_VERSION
        ):
            raise ValueError("validation protocol implementation mismatch")
        execution_cost_policy = validation_protocol.get("execution_cost_policy")
        if not isinstance(execution_cost_policy, dict) or not all(
            (
                execution_cost_policy.get("commission"),
                execution_cost_policy.get("slippage"),
                execution_cost_policy.get("artifact_must_record_exact_model") is True,
            )
        ):
            raise ValueError("validation protocol execution cost policy is incomplete")
        registered_at = validation_protocol.get("registered_at")
        prospective_start = validation_protocol.get("prospective_oos_start")
        minimum_days = validation_protocol.get("minimum_trading_days")
        if not isinstance(registered_at, str) or not isinstance(
            prospective_start,
            str,
        ):
            raise ValueError("validation protocol dates are required")
        try:
            registration_date = date.fromisoformat(registered_at)
            holdout_start_date = date.fromisoformat(prospective_start)
        except ValueError as exc:
            raise ValueError("validation protocol dates must be ISO dates") from exc
        if registration_date >= holdout_start_date:
            raise ValueError("validation protocol must predate prospective OOS")
        if (
            isinstance(minimum_days, bool)
            or not isinstance(minimum_days, int)
            or minimum_days < 63
        ):
            raise ValueError("minimum_trading_days must be at least 63")
        expected_config = json.loads(
            json.dumps(asdict(result.config), sort_keys=True)
        )
        if validation_protocol.get("config") != expected_config:
            raise ValueError("validation protocol config mismatch")
        primary_benchmark = validation_protocol.get("primary_benchmark")
        if primary_benchmark not in benchmarks:
            raise ValueError("validation protocol primary benchmark is unavailable")
        prospective_returns = [
            item
            for item in result.daily_returns
            if item[0] >= prospective_start
        ]
        continuity = assess_collection_continuity(
            validation_protocol,
            (item[0] for item in prospective_returns),
            prospective_observations,
        )
        observed_days = int(continuity["verified_trading_days"])
        holdout_complete = observed_days >= minimum_days
        evaluation_returns = prospective_returns[:observed_days][:minimum_days]
        evaluation_dates = {item[0] for item in evaluation_returns}
        holdout_turnover = sum(
            item.turnover
            for item in result.rebalances
            if item.effective_date in evaluation_dates
        )
        if holdout_complete:
            headline_metrics = _metrics_from_daily_returns(
                evaluation_returns,
                turnover=holdout_turnover,
            )
            headline_benchmarks = {
                name: _metrics_from_daily_returns(
                    [
                        item
                        for item in result.benchmark_daily_returns[name]
                        if item[0] in evaluation_dates
                    ]
                )
                for name in benchmarks
            }
        else:
            headline_metrics = None
            headline_benchmarks = {}

        historical_returns = [
            item for item in result.daily_returns if item[0] < prospective_start
        ]
        if not historical_returns:
            raise ValueError("prospective protocol has no historical diagnostic sample")
        historical_dates = {item[0] for item in historical_returns}
        historical_turnover = sum(
            item.turnover
            for item in result.rebalances
            if item.effective_date in historical_dates
        )
        diagnostic_metrics = _metrics_from_daily_returns(
            historical_returns,
            turnover=historical_turnover,
        )
        diagnostic_benchmark_metrics = {
            name: _metrics_from_daily_returns(
                [
                    item
                    for item in result.benchmark_daily_returns[name]
                    if item[0] in historical_dates
                ]
            )
            for name in benchmarks
        }
        diagnostic_sample_end = historical_returns[-1][0]
        publication_end = (
            evaluation_returns[-1][0]
            if holdout_complete and evaluation_returns
            else diagnostic_sample_end
        )
        published_equity_curve = [
            item for item in result.equity_curve if item[0] <= publication_end
        ]
        published_rebalances = [
            item
            for item in result.rebalances
            if item.effective_date <= publication_end
        ]
        published_folds = [
            item
            for item in result.walk_forward_folds
            if item.test_end < prospective_start
        ]
        protocol_record = dict(validation_protocol)
        protocol_record.update(
            {
                "version": 3,
                "parameter_selection": "pre_registered_no_fit",
                "walk_forward_role": "historical_diagnostic_only",
                "untouched_holdout": True,
                "headline_metrics_scope": "prospective_holdout",
                "diagnostic_metrics_scope": "historical_pre_holdout_only",
            }
        )
        holdout_state = (
            "invalid_data_gap"
            if continuity["state"] == "invalid_data_gap"
            else "completed"
            if holdout_complete
            else "collecting"
        )
        prospective_holdout = {
            "state": holdout_state,
            "start": prospective_start,
            "end": (
                evaluation_returns[-1][0]
                if holdout_complete
                else None
            ),
            "minimum_trading_days": minimum_days,
            "observed_trading_days": observed_days,
            "available_trading_days": len(prospective_returns),
            "metrics_revealed": holdout_complete,
            "collection_continuity": continuity,
        }
    payload = {
        "strategy": "broad_etf_dual_momentum_v1",
        "strategy_implementation_version": STRATEGY_IMPLEMENTATION_VERSION,
        "input_adjustment": "total_return",
        "config": asdict(result.config),
        "execution_cost_model": result.execution_cost_model.as_record(),
        "data_manifest_ids": manifest_ids,
        "code_revision": revision,
        "benchmark_names": benchmarks,
        "benchmark_metrics": headline_benchmarks,
        "metrics": headline_metrics,
        "full_sample_benchmark_metrics": diagnostic_benchmark_metrics,
        "full_sample_metrics": diagnostic_metrics,
        "diagnostic_sample_end": diagnostic_sample_end,
        "rebalances": [asdict(item) for item in published_rebalances],
        "walk_forward_folds": [asdict(item) for item in published_folds],
        "validation_protocol": protocol_record,
        "prospective_holdout": prospective_holdout,
        "equity_curve": published_equity_curve,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    experiment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"experiment:{digest}"))
    destination = Path(output_root) / "gold" / "experiments" / f"{experiment_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_bytes() != canonical:
        raise FileExistsError(f"immutable experiment conflict: {destination}")
    if not destination.exists():
        destination.write_bytes(canonical)
    return destination
