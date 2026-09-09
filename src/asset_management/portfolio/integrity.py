"""Economic semantics and stability checks for portfolio construction."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from asset_management.domain.errors import DataQualityError
from asset_management.domain.economics import ReturnSemanticType

from .models import PortfolioTarget


def _number(value: object, reason: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or (nonnegative and value < 0):
        raise DataQualityError(reason)
    return value


def _vector(value: object, reason: str, *, weights: bool = False) -> tuple[Decimal, ...]:
    if not isinstance(value, tuple) or not value or any(not isinstance(item, Decimal) or not item.is_finite() for item in value):
        raise DataQualityError(reason)
    if weights and (any(item < 0 for item in value) or sum(value) != Decimal(1)):
        raise DataQualityError(reason)
    return value


def _instruments(value: object) -> tuple[str, ...]:
    if (not isinstance(value, tuple) or not value or len(set(value)) != len(value) or
            any(not isinstance(item, str) or not item.strip() for item in value)):
        raise DataQualityError("OPTIMIZER_INSTRUMENT_IDENTITY_INVALID")
    return value


class OptimizationReturnMode(StrEnum):
    ABSOLUTE = "ABSOLUTE"
    STRATEGIC = "STRATEGIC"
    BENCHMARK_ACTIVE = "BENCHMARK_ACTIVE"
    MODEL_RELATIVE_ALPHA = "MODEL_RELATIVE_ALPHA"


@dataclass(frozen=True, slots=True)
class CapitalAllocation:
    """A NAV basis where recognized liabilities cannot be deducted twice."""

    nav: Decimal
    liabilities_recognized_in_nav: Decimal
    off_nav_liability_reserve: Decimal
    risk_budget_fraction: Decimal

    def __post_init__(self) -> None:
        nav = _number(self.nav, "CAPITAL_BASIS_INVALID", nonnegative=True)
        recognized = _number(self.liabilities_recognized_in_nav, "CAPITAL_BASIS_INVALID", nonnegative=True)
        reserve = _number(self.off_nav_liability_reserve, "CAPITAL_BASIS_INVALID", nonnegative=True)
        budget = _number(self.risk_budget_fraction, "CAPITAL_BASIS_INVALID", nonnegative=True)
        if nav <= 0 or recognized > nav or reserve > nav or budget > 1:
            raise DataQualityError("CAPITAL_BASIS_INVALID")

    @property
    def investable_capital(self) -> Decimal:
        return self.nav - self.off_nav_liability_reserve

    @property
    def risk_capital(self) -> Decimal:
        return self.investable_capital * self.risk_budget_fraction


@dataclass(frozen=True, slots=True)
class OptimizationReturnInput:
    """A typed return vector bound to one explicit instrument ordering."""

    mode: OptimizationReturnMode
    instruments: tuple[str, ...]
    forecast_total_return: tuple[Decimal, ...]
    benchmark_weights: tuple[Decimal, ...] | None = None
    model_relative_alpha: tuple[Decimal, ...] | None = None
    pricing_baseline_version: str | None = None
    factor_neutrality_verified: bool = False
    return_semantic_type: ReturnSemanticType = ReturnSemanticType.FORECAST_TOTAL_RETURN_NET

    def __post_init__(self) -> None:
        instruments = _instruments(self.instruments)
        forecasts = _vector(self.forecast_total_return, "OPTIMIZER_RETURN_INPUT_INVALID")
        if (len(instruments) != len(forecasts) or not isinstance(self.mode, OptimizationReturnMode) or
                type(self.factor_neutrality_verified) is not bool or
                self.return_semantic_type not in (ReturnSemanticType.FORECAST_TOTAL_RETURN_GROSS,
                                                   ReturnSemanticType.FORECAST_TOTAL_RETURN_NET)):
            raise DataQualityError("OPTIMIZER_RETURN_INPUT_INVALID")
        active = self.mode in {OptimizationReturnMode.BENCHMARK_ACTIVE, OptimizationReturnMode.MODEL_RELATIVE_ALPHA}
        if active:
            benchmark = _vector(self.benchmark_weights, "OPTIMIZER_BENCHMARK_INVALID", weights=True)
            if len(benchmark) != len(forecasts):
                raise DataQualityError("OPTIMIZER_BENCHMARK_INVALID")
        elif self.benchmark_weights is not None:
            raise DataQualityError("OPTIMIZER_BENCHMARK_UNEXPECTED")
        if self.mode is OptimizationReturnMode.MODEL_RELATIVE_ALPHA:
            alpha = _vector(self.model_relative_alpha, "OPTIMIZER_MODEL_ALPHA_INVALID")
            if (len(alpha) != len(forecasts) or not isinstance(self.pricing_baseline_version, str) or
                    not self.pricing_baseline_version.strip() or not self.factor_neutrality_verified):
                raise DataQualityError("OPTIMIZER_MODEL_ALPHA_ALIGNMENT_INVALID")
        elif (self.model_relative_alpha is not None or self.pricing_baseline_version is not None or
              self.factor_neutrality_verified):
            raise DataQualityError("OPTIMIZER_MODEL_ALPHA_UNEXPECTED")
        object.__setattr__(self, "instruments", instruments)

    @property
    def return_vector(self) -> tuple[Decimal, ...]:
        return self.model_relative_alpha if self.mode is OptimizationReturnMode.MODEL_RELATIVE_ALPHA else self.forecast_total_return

    def expected_return(self, weights: tuple[Decimal, ...]) -> Decimal:
        portfolio = _vector(weights, "OPTIMIZER_WEIGHTS_INVALID", weights=True)
        if len(portfolio) != len(self.forecast_total_return):
            raise DataQualityError("OPTIMIZER_DIMENSION_INVALID")
        exposure = portfolio if self.benchmark_weights is None else tuple(
            portfolio[index] - self.benchmark_weights[index] for index in range(len(portfolio)))
        return sum((exposure[index] * self.return_vector[index] for index in range(len(portfolio))), Decimal(0))


@dataclass(frozen=True, slots=True)
class OptimizationStabilityPolicy:
    cash_instrument: str
    max_weight_jump: Decimal
    max_turnover_sensitivity: Decimal
    corner_weight_limit: Decimal

    def __post_init__(self) -> None:
        if (not isinstance(self.cash_instrument, str) or not self.cash_instrument.strip() or
                any(_number(value, "OPTIMUM_STABILITY_POLICY_INVALID", nonnegative=True) > 1 for value in
                    (self.max_weight_jump, self.max_turnover_sensitivity, self.corner_weight_limit)) or
                self.corner_weight_limit <= 0):
            raise DataQualityError("OPTIMUM_STABILITY_POLICY_INVALID")


@dataclass(frozen=True, slots=True)
class OptimizationStabilityEvidence:
    current: PortfolioTarget
    baseline: PortfolioTarget
    forecast_perturbed: PortfolioTarget
    covariance_perturbed: PortfolioTarget
    policy: OptimizationStabilityPolicy

    def __post_init__(self) -> None:
        values = (self.current, self.baseline, self.forecast_perturbed, self.covariance_perturbed)
        if (not isinstance(self.policy, OptimizationStabilityPolicy) or any(not isinstance(value, PortfolioTarget) for value in values) or
                len({value.instruments for value in values}) != 1 or len(self.current.instruments) < 2 or
                self.policy.cash_instrument not in self.current.instruments or
                any(sum(value.weights) != Decimal(1) or any(weight < 0 for weight in value.weights) for value in values)):
            raise DataQualityError("OPTIMUM_STABILITY_INPUT_INVALID")

    def violations(self) -> tuple[str, ...]:
        baseline, current = self.baseline.weights, self.current.weights
        perturbed = (self.forecast_perturbed.weights, self.covariance_perturbed.weights)
        jumps = [max(abs(candidate[index] - baseline[index]) for index in range(len(baseline))) for candidate in perturbed]
        base_turnover = sum(abs(value - current[index]) for index, value in enumerate(baseline))
        turnover_changes = [abs(sum(abs(value - current[index]) for index, value in enumerate(candidate)) - base_turnover)
                            for candidate in perturbed]
        cash_index = self.current.instruments.index(self.policy.cash_instrument)
        corners = [max(candidate[index] for index in range(len(candidate)) if index != cash_index) >= self.policy.corner_weight_limit
                   for candidate in (baseline, *perturbed)]
        reasons: list[str] = []
        if max(jumps) > self.policy.max_weight_jump:
            reasons.append("WEIGHT_JUMP")
        if max(turnover_changes) > self.policy.max_turnover_sensitivity:
            reasons.append("TURNOVER_SENSITIVITY")
        if any(corners):
            reasons.append("CORNER_SOLUTION")
        return tuple(reasons)

    def require_stable(self) -> None:
        if reasons := self.violations():
            raise DataQualityError("PORTFOLIO_OPTIMUM_UNSTABLE:" + ",".join(reasons))
