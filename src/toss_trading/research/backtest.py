from __future__ import annotations

import hashlib
import json
import math
import statistics
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


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
    commission_bps: float = 1.5
    slippage_bps: float = 2.0

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
        if min(self.commission_bps, self.slippage_bps) < 0:
            raise ValueError("cost assumptions must be nonnegative")


@dataclass(frozen=True)
class Rebalance:
    signal_date: str
    effective_date: str
    scores: dict[str, float]
    target_weights: dict[str, float]
    turnover: float
    cost_fraction: float


@dataclass(frozen=True)
class BacktestResult:
    config: DualMomentumConfig
    equity_curve: tuple[tuple[str, float], ...]
    daily_returns: tuple[tuple[str, float], ...]
    rebalances: tuple[Rebalance, ...]
    metrics: dict[str, float]


def _number(value: str) -> float:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid total-return index: {value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"total-return index must be finite and positive: {value!r}")
    return float(result)


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


def run_dual_momentum_backtest(
    points: Iterable[PricePoint],
    config: DualMomentumConfig,
) -> BacktestResult:
    """Run a monthly 12-1-style total-return momentum baseline with next-day execution."""

    config.validate()
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
    cost_rate = (config.commission_bps + config.slippage_bps) / 10_000.0

    for index in range(1, len(dates)):
        current_date = dates[index]
        transaction_cost = 0.0
        if pending is not None:
            signal_date, scores, target = pending
            turnover = _turnover(weights, target)
            transaction_cost = turnover * cost_rate
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
                )
            )
            pending = None

        previous_date = dates[index - 1]
        gross_return = sum(
            weight
            * (
                panel[current_date][symbol] / panel[previous_date][symbol]
                - 1.0
            )
            for symbol, weight in weights.items()
        )
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
                if availability[(recent_date, symbol)][:10] > current_date:
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
    )


def write_experiment_record(
    result: BacktestResult,
    *,
    output_root: str | Path,
    data_manifest_ids: Iterable[str],
    code_revision: str,
    benchmark_names: Iterable[str],
) -> Path:
    """Persist a reproducible experiment record without mutating prior results."""

    payload = {
        "strategy": "broad_etf_dual_momentum_v1",
        "config": asdict(result.config),
        "data_manifest_ids": sorted(set(data_manifest_ids)),
        "code_revision": code_revision,
        "benchmark_names": sorted(set(benchmark_names)),
        "metrics": result.metrics,
        "rebalances": [asdict(item) for item in result.rebalances],
        "equity_curve": result.equity_curve,
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
