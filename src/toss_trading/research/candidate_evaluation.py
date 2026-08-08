from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from .backtest import (
    BacktestResult,
    DualMomentumConfig,
    PricePoint,
    run_dual_momentum_backtest,
    _metrics_from_daily_returns,
)
from .costs import ExecutionCostModel


EVALUATION_SCHEMA = "historical-candidate-evaluation-v1"
PRIMARY_BENCHMARK = "SPY buy-and-hold"


def _config(payload: dict[str, Any]) -> DualMomentumConfig:
    config = payload.get("config")
    if not isinstance(config, dict):
        raise ValueError("hypothesis config is missing")
    return DualMomentumConfig(
        candidate_symbols=tuple(config["candidate_symbols"]),
        cash_symbol=str(config["cash_symbol"]),
        lookback_trading_days=int(config["lookback_trading_days"]),
        skip_recent_trading_days=int(config["skip_recent_trading_days"]),
        top_k=int(config["top_k"]),
        minimum_absolute_momentum=float(config["minimum_absolute_momentum"]),
        walk_forward_train_days=int(config["walk_forward_train_days"]),
        walk_forward_test_days=int(config["walk_forward_test_days"]),
    )


def _common_history(
    points: Iterable[PricePoint], *, required_symbols: set[str]
) -> list[PricePoint]:
    materialized = list(points)
    symbols_by_date: dict[str, set[str]] = {}
    for point in materialized:
        symbols_by_date.setdefault(point.date, set()).add(point.symbol)
    complete_dates = {
        item_date
        for item_date, symbols in symbols_by_date.items()
        if required_symbols <= symbols
    }
    if not complete_dates:
        raise ValueError("candidate has no common total-return history")
    return [point for point in materialized if point.date in complete_dates]


def _paired_excess_returns(result: BacktestResult) -> list[float]:
    benchmark = result.benchmark_daily_returns.get(PRIMARY_BENCHMARK)
    if benchmark is None:
        raise ValueError("SPY benchmark is unavailable")
    benchmark_by_date = dict(benchmark)
    pairs = [
        value - benchmark_by_date[item_date]
        for item_date, value in result.daily_returns
        if item_date in benchmark_by_date
    ]
    if len(pairs) != len(result.daily_returns):
        raise ValueError("strategy and benchmark dates are not aligned")
    if len(pairs) < 2:
        raise ValueError("candidate has insufficient paired returns")
    return pairs


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _block_sample_mean(
    values: list[float], *, block_days: int, generator: random.Random
) -> float:
    total = 0.0
    remaining = len(values)
    while remaining:
        take = min(block_days, remaining)
        start = generator.randrange(len(values))
        total += sum(values[(start + offset) % len(values)] for offset in range(take))
        remaining -= take
    return total / len(values)


def block_bootstrap_test(
    excess_returns: list[float],
    *,
    samples: int,
    block_days: int,
    family_size: int,
    familywise_alpha: float,
    seed_material: str,
) -> dict[str, float | int | bool]:
    """Paired block bootstrap with a Bonferroni family-wise correction."""

    if samples < 200 or not 1 <= block_days <= len(excess_returns):
        raise ValueError("invalid block-bootstrap settings")
    if family_size < 1:
        raise ValueError("family_size must be positive")
    observed = statistics.fmean(excess_returns)
    centered = [value - observed for value in excess_returns]
    seed = int.from_bytes(
        hashlib.sha256(seed_material.encode("utf-8")).digest()[:8], "big"
    )
    generator = random.Random(seed)
    sampled_means: list[float] = []
    null_exceedances = 0
    for _ in range(samples):
        sampled_means.append(
            _block_sample_mean(
                excess_returns,
                block_days=block_days,
                generator=generator,
            )
        )
        if (
            _block_sample_mean(
                centered,
                block_days=block_days,
                generator=generator,
            )
            >= observed
        ):
            null_exceedances += 1
    raw_p = (null_exceedances + 1) / (samples + 1)
    adjusted_p = min(1.0, raw_p * family_size)
    lower = _quantile(sampled_means, familywise_alpha / 2.0)
    upper = _quantile(sampled_means, 1.0 - familywise_alpha / 2.0)
    return {
        "observations": len(excess_returns),
        "block_days": block_days,
        "samples": samples,
        "mean_daily_excess": observed,
        "annualized_mean_excess": observed * 252.0,
        "confidence_interval_daily_lower": lower,
        "confidence_interval_daily_upper": upper,
        "raw_one_sided_p_value": raw_p,
        "bonferroni_family_size": family_size,
        "bonferroni_adjusted_p_value": adjusted_p,
        "familywise_alpha": familywise_alpha,
        "passes_adjusted_test": adjusted_p <= familywise_alpha and lower > 0.0,
    }


def evaluate_hypothesis(
    hypothesis: dict[str, Any],
    *,
    points: Iterable[PricePoint],
    policy: dict[str, Any],
    family_size: int,
    data_manifest_ids: list[str],
    code_revision: str,
    run_id: str,
    evaluated_at: str | None = None,
    execution_cost_model: ExecutionCostModel,
) -> dict[str, Any]:
    """Evaluate one registered hypothesis without granting promotion authority."""

    config = _config(hypothesis)
    required = set(config.candidate_symbols) | {config.cash_symbol, "SPY"}
    aligned = _common_history(points, required_symbols=required)
    result = run_dual_momentum_backtest(
        aligned,
        config,
        execution_cost_model=execution_cost_model,
    )
    statistical_test = block_bootstrap_test(
        _paired_excess_returns(result),
        samples=int(policy["bootstrap_samples"]),
        block_days=int(policy["bootstrap_block_days"]),
        family_size=family_size,
        familywise_alpha=float(policy["familywise_alpha"]),
        seed_material=f"{hypothesis['hypothesis_id']}:{run_id}",
    )
    stress_multiplier = float(policy["cost_stress_multiplier"])
    stressed = run_dual_momentum_backtest(
        aligned,
        config,
        execution_cost_model=result.execution_cost_model.stressed(stress_multiplier),
    )
    stressed_excess = statistics.fmean(_paired_excess_returns(stressed)) * 252.0
    fold_count = len(result.walk_forward_folds)
    positive_folds = sum(
        1
        for fold in result.walk_forward_folds
        if fold.passed_relative_return
    )
    positive_fold_ratio = positive_folds / fold_count if fold_count else 0.0
    gates = {
        "minimum_walk_forward_folds": fold_count
        >= int(policy["minimum_walk_forward_folds"]),
        "benchmark_outperformance_ratio": positive_fold_ratio
        >= float(policy["minimum_benchmark_outperformance_ratio"]),
        "multiple_testing_adjusted_benchmark": bool(
            statistical_test["passes_adjusted_test"]
        ),
        "double_cost_stress_excess_positive": stressed_excess > 0.0,
    }
    qualified = all(gates.values())
    return {
        "evaluation_schema": EVALUATION_SCHEMA,
        "evaluated_at": evaluated_at or datetime.now(timezone.utc).isoformat(),
        "state": (
            "awaiting_prospective_observation"
            if qualified
            else "historical_not_qualified"
        ),
        "historical_screen_passed": qualified,
        "promotion_authorized": False,
        "execution_authorized": False,
        "next_required_stage": (
            "sealed_prospective_oos"
            if qualified
            else "none_retest_only_with_new_scheduled_data"
        ),
        "historical_cutoff": result.daily_returns[-1][0],
        "gates": gates,
        "walk_forward": {
            "folds": fold_count,
            "benchmark": PRIMARY_BENCHMARK,
            "benchmark_outperforming_folds": positive_folds,
            "benchmark_outperformance_ratio": positive_fold_ratio,
        },
        "statistical_test": statistical_test,
        "cost_stress": {
            "multiplier": stress_multiplier,
            "annualized_mean_excess": stressed_excess,
        },
        "metrics": result.metrics,
        "benchmark_metrics": {PRIMARY_BENCHMARK: result.benchmark_metrics[PRIMARY_BENCHMARK]},
        "data_manifest_ids": sorted(set(data_manifest_ids)),
        "code_revision": code_revision,
        "config": asdict(config),
        "execution_cost_model": result.execution_cost_model.as_record(),
    }


def evaluate_prospective_hypothesis(
    hypothesis: dict[str, Any],
    *,
    protocol: dict[str, Any],
    points: Iterable[PricePoint],
    policy: dict[str, Any],
    family_size: int,
    data_manifest_ids: list[str],
    code_revision: str,
    run_id: str,
    evaluated_at: str | None = None,
    execution_cost_model: ExecutionCostModel,
) -> dict[str, Any]:
    """Observe a sealed future window and hide metrics until it is complete."""

    config = _config(hypothesis)
    config_hash = hashlib.sha256(
        json.dumps(
            hypothesis["config"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if protocol.get("config_sha256") != config_hash:
        raise ValueError("prospective protocol config does not match hypothesis")
    cutoff = str(protocol["historical_cutoff"])
    required = set(config.candidate_symbols) | {config.cash_symbol, "SPY"}
    aligned = _common_history(points, required_symbols=required)
    result = run_dual_momentum_backtest(
        aligned,
        config,
        execution_cost_model=execution_cost_model,
    )
    prospective_returns = [item for item in result.daily_returns if item[0] > cutoff]
    prospective_rebalances = [
        item for item in result.rebalances if item.effective_date > cutoff
    ]
    minimum_days = int(protocol["minimum_trading_days"])
    minimum_rebalances = int(protocol["minimum_rebalances"])
    completion_index: int | None = None
    for index, (item_date, _) in enumerate(prospective_returns, start=1):
        rebalance_count = sum(
            1 for item in prospective_rebalances if item.effective_date <= item_date
        )
        if index >= minimum_days and rebalance_count >= minimum_rebalances:
            completion_index = index
            break
    observed_days = len(prospective_returns)
    observed_rebalances = len(prospective_rebalances)
    common = {
        "evaluation_schema": EVALUATION_SCHEMA,
        "evaluated_at": evaluated_at or datetime.now(timezone.utc).isoformat(),
        "historical_screen_passed": True,
        "historical_cutoff": cutoff,
        "promotion_authorized": False,
        "execution_authorized": False,
        "prospective_observation": {
            "state": "collecting" if completion_index is None else "completed",
            "observed_trading_days": observed_days,
            "minimum_trading_days": minimum_days,
            "observed_rebalances": observed_rebalances,
            "minimum_rebalances": minimum_rebalances,
            "metrics_revealed": completion_index is not None,
        },
        "paper_stage": {
            "state": "not_started",
            "reason": "paper_broker_execution_evidence_not_configured",
        },
        "shadow_stage": {"state": "not_started"},
        "data_manifest_ids": sorted(set(data_manifest_ids)),
        "code_revision": code_revision,
        "config": asdict(config),
        "execution_cost_model": result.execution_cost_model.as_record(),
    }
    if completion_index is None:
        return {
            **common,
            "state": "prospective_collecting",
            "next_required_stage": "continue_sealed_prospective_oos",
            "prospective_metrics": None,
            "prospective_benchmark_metrics": None,
            "statistical_test": None,
        }
    evaluation_returns = prospective_returns[:completion_index]
    evaluation_dates = {item[0] for item in evaluation_returns}
    benchmark_all = dict(result.benchmark_daily_returns[PRIMARY_BENCHMARK])
    benchmark_returns = [
        (item_date, benchmark_all[item_date])
        for item_date, _ in evaluation_returns
    ]
    paired_excess = [
        value - benchmark_all[item_date] for item_date, value in evaluation_returns
    ]
    statistical_test = block_bootstrap_test(
        paired_excess,
        samples=int(policy["bootstrap_samples"]),
        block_days=int(policy["bootstrap_block_days"]),
        family_size=family_size,
        familywise_alpha=float(policy["familywise_alpha"]),
        seed_material=f"prospective:{hypothesis['hypothesis_id']}:{cutoff}",
    )
    stress_multiplier = float(policy["cost_stress_multiplier"])
    stressed = run_dual_momentum_backtest(
        aligned,
        config,
        execution_cost_model=result.execution_cost_model.stressed(stress_multiplier),
    )
    stressed_by_date = dict(stressed.daily_returns)
    stress_excess = statistics.fmean(
        stressed_by_date[item_date] - benchmark_all[item_date]
        for item_date in sorted(evaluation_dates)
    ) * 252.0
    gates = {
        "multiple_testing_adjusted_benchmark": bool(
            statistical_test["passes_adjusted_test"]
        ),
        "double_cost_stress_excess_positive": stress_excess > 0.0,
    }
    passed = all(gates.values())
    return {
        **common,
        "state": (
            "prospective_complete_awaiting_paper_infrastructure"
            if passed
            else "prospective_not_qualified"
        ),
        "next_required_stage": (
            "paper_execution_validation"
            if passed
            else "none_rejected_by_prospective_evidence"
        ),
        "prospective_metrics": _metrics_from_daily_returns(evaluation_returns),
        "prospective_benchmark_metrics": _metrics_from_daily_returns(
            benchmark_returns
        ),
        "statistical_test": statistical_test,
        "cost_stress": {
            "multiplier": stress_multiplier,
            "annualized_mean_excess": stress_excess,
        },
        "gates": gates,
    }
