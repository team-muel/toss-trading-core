"""AMA-49 policy-defined stress scenarios with explicit economic context."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from asset_management.domain.decimal import exact_decimal
from asset_management.domain.errors import DataQualityError, InvariantViolation


class ValuationConvention(StrEnum):
    MARK_TO_MARKET = "MARK_TO_MARKET"
    TOTAL_RETURN = "TOTAL_RETURN"


REQUIRED_STRESS_SCENARIOS = (
    "US_EQUITY_MINUS_10", "NASDAQ_MINUS_20", "SEMICONDUCTOR_MINUS_30",
    "LONG_RATE_PLUS_100BP", "CREDIT_SPREAD_WIDENING", "USDKRW_SHOCK",
    "CORRELATION_SPIKE", "LIQUIDITY_DETERIORATION", "VOLUME_TRIPLE",
)


def _decimal_map(values: Mapping[str, Decimal], reason: str) -> dict[str, Decimal]:
    if not values or any(not isinstance(key, str) or not key.strip() for key in values):
        raise InvariantViolation(reason)
    return {key: exact_decimal(value) for key, value in values.items()}


@dataclass(frozen=True, slots=True)
class StressScenario:
    """A scenario changes risk inputs; it never creates an expected-return estimate."""
    scenario_id: str
    asset_shocks: Mapping[str, Decimal]
    fx_shocks: Mapping[str, Decimal]
    currency_basis: str
    valuation_convention: ValuationConvention
    holding_horizon_days: int
    formula_version: str
    liquidity_multiplier: Decimal = Decimal(1)
    correlation_multiplier: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        if (self.scenario_id not in REQUIRED_STRESS_SCENARIOS or not self.currency_basis.strip() or
                not isinstance(self.valuation_convention, ValuationConvention) or
                type(self.holding_horizon_days) is not int or self.holding_horizon_days < 1 or
                not self.formula_version.strip()):
            raise InvariantViolation("STRESS_SCENARIO_CONTEXT_INVALID")
        assets = _decimal_map(self.asset_shocks, "STRESS_ASSET_SHOCKS_INVALID")
        fx = _decimal_map(self.fx_shocks, "STRESS_FX_SHOCKS_INVALID")
        liquidity = exact_decimal(self.liquidity_multiplier)
        correlation = exact_decimal(self.correlation_multiplier)
        if liquidity < 1 or correlation < 1:
            raise InvariantViolation("STRESS_MULTIPLIER_INVALID")
        object.__setattr__(self, "asset_shocks", assets)
        object.__setattr__(self, "fx_shocks", fx)
        object.__setattr__(self, "liquidity_multiplier", liquidity)
        object.__setattr__(self, "correlation_multiplier", correlation)

    def payload(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "asset_shocks": {key: str(value) for key, value in sorted(self.asset_shocks.items())},
            "fx_shocks": {key: str(value) for key, value in sorted(self.fx_shocks.items())},
            "currency_basis": self.currency_basis,
            "valuation_convention": self.valuation_convention.value,
            "holding_horizon_days": self.holding_horizon_days,
            "formula_version": self.formula_version,
            "liquidity_multiplier": str(self.liquidity_multiplier),
            "correlation_multiplier": str(self.correlation_multiplier),
        }


@dataclass(frozen=True, slots=True)
class StressResult:
    scenario_id: str
    loss_fraction: Decimal
    fx_shocks: Mapping[str, Decimal]
    currency_basis: str
    valuation_convention: ValuationConvention
    holding_horizon_days: int
    formula_version: str
    liquidity_multiplier: Decimal
    correlation_multiplier: Decimal

    def __post_init__(self) -> None:
        loss = exact_decimal(self.loss_fraction)
        if loss < 0 or not self.currency_basis.strip() or self.holding_horizon_days < 1:
            raise InvariantViolation("STRESS_RESULT_INVALID")
        object.__setattr__(self, "loss_fraction", loss)
        object.__setattr__(self, "fx_shocks", _decimal_map(self.fx_shocks, "STRESS_RESULT_FX_INVALID"))


def stress_loss(weights: tuple[Decimal, ...], instruments: tuple[str, ...], scenario: StressScenario,
                *, currency_basis: str, valuation_convention: ValuationConvention,
                holding_horizon_days: int) -> StressResult:
    """Evaluate a predeclared stress case only in its declared economic context."""
    normalized = tuple(exact_decimal(value) for value in weights)
    if (not isinstance(scenario, StressScenario) or len(normalized) != len(instruments) or
            len(set(instruments)) != len(instruments) or any(not item.strip() for item in instruments) or
            any(item not in scenario.asset_shocks for item in instruments) or
            currency_basis != scenario.currency_basis or valuation_convention is not scenario.valuation_convention or
            holding_horizon_days != scenario.holding_horizon_days):
        raise DataQualityError("STRESS_INPUT_CONTEXT_INVALID")
    pnl = sum((normalized[index] * scenario.asset_shocks[instrument]
               for index, instrument in enumerate(instruments)), Decimal(0))
    return StressResult(scenario.scenario_id, max(Decimal(0), -pnl), scenario.fx_shocks,
                        scenario.currency_basis, scenario.valuation_convention,
                        scenario.holding_horizon_days, scenario.formula_version,
                        scenario.liquidity_multiplier, scenario.correlation_multiplier)


def validate_scenario_set(scenarios: tuple[StressScenario, ...]) -> None:
    if {scenario.scenario_id for scenario in scenarios} != set(REQUIRED_STRESS_SCENARIOS):
        raise DataQualityError("STRESS_SCENARIO_SET_INCOMPLETE")


def gap_stress(weight: Decimal, gap_return: Decimal) -> Decimal:
    weight = exact_decimal(weight)
    gap_return = exact_decimal(gap_return)
    return max(Decimal(0), -weight * gap_return)
