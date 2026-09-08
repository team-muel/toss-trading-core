"""Shared economic meanings. No broker, model, or portfolio authority lives here."""
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from collections.abc import Mapping

from .errors import DataQualityError
from .scalars import Currency


class CurrencyBasis(StrEnum):
    LOCAL = "LOCAL"
    BASE = "BASE"
    HEDGED = "HEDGED"


class ReturnSemanticType(StrEnum):
    PRICING_BASELINE_RETURN = "PRICING_BASELINE_RETURN"
    FORECAST_TOTAL_RETURN_GROSS = "FORECAST_TOTAL_RETURN_GROSS"
    FORECAST_TOTAL_RETURN_NET = "FORECAST_TOTAL_RETURN_NET"
    MODEL_RELATIVE_ALPHA = "MODEL_RELATIVE_ALPHA"
    EXPECTED_BENCHMARK_ACTIVE_RETURN = "EXPECTED_BENCHMARK_ACTIVE_RETURN"
    REALIZED_ACTIVE_RETURN = "REALIZED_ACTIVE_RETURN"
    REGRESSION_ALPHA = "REGRESSION_ALPHA"


class ReturnMetricStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_MATURED = "NOT_MATURED"


class ReturnUnit(StrEnum):
    TOTAL_RETURN = "TOTAL_RETURN"
    EXCESS_RETURN = "EXCESS_RETURN"
    ACTIVE_RETURN = "ACTIVE_RETURN"
    REGRESSION_INTERCEPT = "REGRESSION_INTERCEPT"


class RiskContributionType(StrEnum):
    VARIANCE = "VARIANCE"
    VOLATILITY = "VOLATILITY"


RETURN_UNITS = MappingProxyType({
    ReturnSemanticType.PRICING_BASELINE_RETURN: ReturnUnit.TOTAL_RETURN,
    ReturnSemanticType.FORECAST_TOTAL_RETURN_GROSS: ReturnUnit.TOTAL_RETURN,
    ReturnSemanticType.FORECAST_TOTAL_RETURN_NET: ReturnUnit.TOTAL_RETURN,
    ReturnSemanticType.MODEL_RELATIVE_ALPHA: ReturnUnit.EXCESS_RETURN,
    ReturnSemanticType.EXPECTED_BENCHMARK_ACTIVE_RETURN: ReturnUnit.ACTIVE_RETURN,
    ReturnSemanticType.REALIZED_ACTIVE_RETURN: ReturnUnit.ACTIVE_RETURN,
    ReturnSemanticType.REGRESSION_ALPHA: ReturnUnit.REGRESSION_INTERCEPT,
})


class BalanceSemanticType(StrEnum):
    ACCOUNTING_NAV = "ACCOUNTING_NAV"
    INTERNAL_FREE_CASH = "INTERNAL_FREE_CASH"
    BROKER_BUYING_POWER = "BROKER_BUYING_POWER"
    EXECUTABLE_BUY_LIMIT = "EXECUTABLE_BUY_LIMIT"
    INVESTABLE_CAPITAL = "INVESTABLE_CAPITAL"
    RISK_CAPITAL = "RISK_CAPITAL"


class EconomicUnit(StrEnum):
    RETURN_SQUARED = "RETURN_SQUARED"
    RETURN = "RETURN"
    MONEY = "MONEY"


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise DataQualityError("ECONOMIC_VERSION_REQUIRED")
    return value


@dataclass(frozen=True, slots=True)
class EconomicValue:
    """Versioned numeric boundary; missing values cannot masquerade as zero."""

    semantic_type: ReturnSemanticType | RiskContributionType | BalanceSemanticType
    value: Decimal | None
    status: ReturnMetricStatus
    currency: Currency
    currency_basis: CurrencyBasis
    forecast_horizon: int
    unit: ReturnUnit | EconomicUnit
    formula_version: str
    reference_version: str

    def __post_init__(self):
        if (not isinstance(self.currency, Currency) or not isinstance(self.currency_basis, CurrencyBasis)
                or type(self.forecast_horizon) is not int or self.forecast_horizon not in (21, 63, 126, 252)
                or not isinstance(self.status, ReturnMetricStatus)):
            raise DataQualityError("ECONOMIC_CONTEXT_INVALID")
        if isinstance(self.semantic_type, ReturnSemanticType):
            expected = RETURN_UNITS[self.semantic_type]
        elif isinstance(self.semantic_type, RiskContributionType):
            expected = EconomicUnit.RETURN_SQUARED if self.semantic_type is RiskContributionType.VARIANCE else EconomicUnit.RETURN
        elif isinstance(self.semantic_type, BalanceSemanticType):
            expected = EconomicUnit.MONEY
        else:
            raise DataQualityError("ECONOMIC_SEMANTIC_UNKNOWN")
        if self.unit is not expected:
            raise DataQualityError("ECONOMIC_UNIT_MISMATCH")
        _text(self.formula_version)
        _text(self.reference_version)
        if self.status is ReturnMetricStatus.AVAILABLE:
            if not isinstance(self.value, Decimal) or not self.value.is_finite():
                raise DataQualityError("ECONOMIC_VALUE_INVALID")
        elif self.value is not None:
            raise DataQualityError("ECONOMIC_STATUS_VALUE_CONFLICT")

    def payload(self):
        return dict(contract_version="economic-value@1", semantic_type=self.semantic_type.value,
                    value=None if self.value is None else str(self.value), status=self.status.value,
                    currency=self.currency.value, currency_basis=self.currency_basis.value,
                    forecast_horizon=self.forecast_horizon, unit=self.unit.value,
                    formula_version=self.formula_version, reference_version=self.reference_version)


def require_aligned(*values):
    """Compare currency and time conventions before any economic arithmetic."""
    if not values or any(not isinstance(v, EconomicValue) for v in values):
        raise DataQualityError("ECONOMIC_INPUT_REQUIRED")
    if any(v.status is not ReturnMetricStatus.AVAILABLE for v in values):
        raise DataQualityError("ECONOMIC_VALUE_UNAVAILABLE")
    if len({(v.currency, v.currency_basis, v.forecast_horizon) for v in values}) != 1:
        raise DataQualityError("ECONOMIC_CONTEXT_MISMATCH")


def model_relative_alpha(forecast, baseline, *, formula_version):
    require_aligned(forecast, baseline)
    if (forecast.semantic_type is not ReturnSemanticType.FORECAST_TOTAL_RETURN_NET or
            baseline.semantic_type is not ReturnSemanticType.PRICING_BASELINE_RETURN):
        raise DataQualityError("MODEL_ALPHA_INPUT_SEMANTICS_INVALID")
    return EconomicValue(ReturnSemanticType.MODEL_RELATIVE_ALPHA, forecast.value - baseline.value,
                         ReturnMetricStatus.AVAILABLE, forecast.currency, forecast.currency_basis,
                         forecast.forecast_horizon, ReturnUnit.EXCESS_RETURN, formula_version,
                         baseline.reference_version)


def expected_benchmark_active_return(forecasts, weights, benchmark_weights, *, benchmark_version, formula_version):
    """Instrument-keyed inputs prevent silent positional misalignment."""
    if (not isinstance(forecasts, Mapping) or not forecasts or
            any(not isinstance(k, str) or not k.strip() for k in forecasts)):
        raise DataQualityError("BENCHMARK_FORECASTS_REQUIRED")
    require_aligned(*forecasts.values())
    names = sorted(forecasts)
    first = forecasts[names[0]]
    if (len({v.semantic_type for v in forecasts.values()}) != 1 or first.semantic_type not in
            (ReturnSemanticType.FORECAST_TOTAL_RETURN_GROSS, ReturnSemanticType.FORECAST_TOTAL_RETURN_NET)):
        raise DataQualityError("BENCHMARK_FORECAST_SEMANTICS_INVALID")
    for vector in (weights, benchmark_weights):
        if (not isinstance(vector, Mapping) or set(vector) != set(forecasts) or
                any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 for v in vector.values()) or
                sum(vector.values()) != Decimal(1)):
            raise DataQualityError("BENCHMARK_WEIGHTS_INVALID")
    return EconomicValue(ReturnSemanticType.EXPECTED_BENCHMARK_ACTIVE_RETURN,
                         sum(((weights[k]-benchmark_weights[k])*forecasts[k].value for k in names), Decimal(0)),
                         ReturnMetricStatus.AVAILABLE, first.currency, first.currency_basis,
                         first.forecast_horizon, ReturnUnit.ACTIVE_RETURN, formula_version, _text(benchmark_version))


def require_same_unit(*values):
    require_aligned(*values)
    if len({v.unit for v in values}) != 1:
        raise DataQualityError("ECONOMIC_COMPARISON_UNIT_MISMATCH")


def executable_buy_limit(free_cash, buying_power, *, formula_version):
    require_aligned(free_cash, buying_power)
    if (free_cash.semantic_type is not BalanceSemanticType.INTERNAL_FREE_CASH or
            buying_power.semantic_type is not BalanceSemanticType.BROKER_BUYING_POWER or
            free_cash.value < 0 or buying_power.value < 0):
        raise DataQualityError("BUY_LIMIT_INPUT_SEMANTICS_INVALID")
    return EconomicValue(BalanceSemanticType.EXECUTABLE_BUY_LIMIT, min(free_cash.value, buying_power.value),
                         ReturnMetricStatus.AVAILABLE, free_cash.currency, free_cash.currency_basis,
                         free_cash.forecast_horizon, EconomicUnit.MONEY, formula_version, buying_power.reference_version)
