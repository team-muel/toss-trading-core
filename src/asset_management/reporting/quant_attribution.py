"""Mandate-bound forecast-to-realization active-return attribution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import InvestorMandateRegistry


_PNL_COMPONENTS = ("asset_allocation", "factor", "security_selection", "timing", "execution", "fx", "tax", "fees", "residual")


def _decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantViolation("QUANT_ATTRIBUTION_NUMERIC_INVALID")
    return value


def _weights(values: Mapping[str, Decimal], assets: tuple[str, ...]) -> None:
    if (set(values) != set(assets) or any(not isinstance(value, Decimal) or not value.is_finite() for value in values.values()) or
            sum(values.values()) != Decimal(1)):
        raise InvariantViolation("QUANT_ATTRIBUTION_WEIGHT_INVALID")


def _active(weights: Mapping[str, Decimal], benchmark: Mapping[str, Decimal], forecasts: Mapping[str, Decimal]) -> Decimal:
    return sum((weights[key] - benchmark[key]) * forecasts[key] for key in forecasts)


def _efficiency(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator != 0 and numerator * denominator > 0 else None


@dataclass(frozen=True, slots=True)
class QuantAttributionInput:
    strategy_key: str
    decision_id: str
    mandate_key: str
    benchmark_key: str
    evaluated_at: datetime
    forecast_version: str
    optimizer_policy_key: str
    execution_policy_key: str
    lineage_ids: tuple[str, ...]
    forecast_total_returns: Mapping[str, Decimal]
    reference_weights: Mapping[str, Decimal]
    constrained_weights: Mapping[str, Decimal]
    executed_weights: Mapping[str, Decimal]
    benchmark_weights: Mapping[str, Decimal]
    expected_implementation_cost: Decimal
    realized_portfolio_total_return: Decimal
    realized_benchmark_total_return: Decimal
    pnl_contributions: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        if (any(not isinstance(value, str) or not value.strip() for value in
                (self.strategy_key, self.decision_id, self.mandate_key, self.benchmark_key,
                 self.forecast_version, self.optimizer_policy_key, self.execution_policy_key)) or
                not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None or
                not self.lineage_ids or len(set(self.lineage_ids)) != len(self.lineage_ids) or
                any(not isinstance(value, str) or not value.strip() for value in self.lineage_ids) or
                not self.forecast_total_returns):
            raise InvariantViolation("QUANT_ATTRIBUTION_INPUT_INVALID")
        assets = tuple(self.forecast_total_returns)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in self.forecast_total_returns.values()):
            raise InvariantViolation("QUANT_ATTRIBUTION_NUMERIC_INVALID")
        for weights in (self.reference_weights, self.constrained_weights, self.executed_weights, self.benchmark_weights):
            _weights(weights, assets)
        if set(self.pnl_contributions) != set(_PNL_COMPONENTS) or any(not isinstance(value, Decimal) or not value.is_finite() for value in self.pnl_contributions.values()):
            raise InvariantViolation("QUANT_ATTRIBUTION_PNL_INVALID")
        if _decimal(self.expected_implementation_cost) < 0:
            raise InvariantViolation("QUANT_ATTRIBUTION_COST_INVALID")
        _decimal(self.realized_portfolio_total_return); _decimal(self.realized_benchmark_total_return)
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))
        object.__setattr__(self, "lineage_ids", tuple(sorted(self.lineage_ids)))


@dataclass(frozen=True, slots=True)
class QuantAttributionResult:
    payload: Mapping[str, object]
    content_hash: str


def quantify_attribution(registry: InvestorMandateRegistry, inputs: QuantAttributionInput) -> QuantAttributionResult:
    benchmark = registry.require_performance_benchmark(inputs.mandate_key, benchmark_key=inputs.benchmark_key, at=inputs.evaluated_at)
    reference = _active(inputs.reference_weights, inputs.benchmark_weights, inputs.forecast_total_returns)
    portfolio = _active(inputs.constrained_weights, inputs.benchmark_weights, inputs.forecast_total_returns)
    executed = _active(inputs.executed_weights, inputs.benchmark_weights, inputs.forecast_total_returns) - inputs.expected_implementation_cost
    realized = inputs.realized_portfolio_total_return - inputs.realized_benchmark_total_return
    body = {"strategy_key": inputs.strategy_key, "decision_id": inputs.decision_id, "mandate_key": inputs.mandate_key,
            "benchmark_key": inputs.benchmark_key, "benchmark_total_return": benchmark.total_return,
            "evaluated_at": inputs.evaluated_at.isoformat(), "forecast_version": inputs.forecast_version,
            "optimizer_policy_key": inputs.optimizer_policy_key, "execution_policy_key": inputs.execution_policy_key,
            "lineage_ids": list(inputs.lineage_ids), "reference_expected_active_return": str(reference),
            "portfolio_expected_active_return": str(portfolio), "executed_expected_active_return": str(executed),
            "realized_active_return": str(realized), "constraint_drag": str(reference - portfolio),
            "execution_drag": str(portfolio - executed), "realization_gap": str(executed - realized),
            "portfolio_transfer_efficiency": None if _efficiency(portfolio, reference) is None else str(_efficiency(portfolio, reference)),
            "execution_efficiency": None if _efficiency(executed, portfolio) is None else str(_efficiency(executed, portfolio)),
            "pnl_contributions": {key: str(inputs.pnl_contributions[key]) for key in _PNL_COMPONENTS}}
    return QuantAttributionResult(body, digest(canonical(body)))
