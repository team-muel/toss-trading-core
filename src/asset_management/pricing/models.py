"""Phase 13 pricing contracts; outputs are not orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.quality.models import QualityStatus
from asset_management.domain.horizon import DECISION_HORIZONS, SignalValidity
from asset_management.domain.economics import EconomicValue, ReturnSemanticType, ReturnMetricStatus, ReturnUnit
from asset_management.domain.errors import DataQualityError


HORIZONS = DECISION_HORIZONS


@dataclass(frozen=True)
class RiskFreePoint:
    as_of: datetime
    horizon: int
    annualized_rate: Decimal
    source: str
    quality: QualityStatus
    currency: str = "USD"
    day_count: str = "BUS/252"
    compounding: str = "EFFECTIVE_ANNUAL"
    formula_version: str = "risk-free-curve@2"
    uncertainty: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("RISK_FREE_AS_OF_NOT_AWARE")
        if (self.horizon not in HORIZONS or not self.annualized_rate.is_finite() or
                not self.source.strip() or not self.currency.strip() or
                self.day_count != "BUS/252" or self.compounding != "EFFECTIVE_ANNUAL" or
                not self.formula_version.strip() or not self.uncertainty.is_finite() or
                self.uncertainty < 0):
            raise ValueError("RISK_FREE_POINT_INVALID")


@dataclass(frozen=True)
class BetaEstimate:
    beta: Decimal
    raw_beta: Decimal
    standard_error: Decimal
    lookback: int
    observation_count: int
    r_squared: Decimal
    as_of: datetime
    quality: QualityStatus
    shrinkage_weight: Decimal

    def __post_init__(self) -> None:
        values = (self.beta, self.raw_beta, self.standard_error, self.r_squared,
                  self.shrinkage_weight)
        if (any(not value.is_finite() for value in values) or self.standard_error < 0 or
                self.lookback < self.observation_count or self.observation_count < 3 or
                not Decimal(0) <= self.r_squared <= Decimal(1) or
                not Decimal(0) <= self.shrinkage_weight <= Decimal(1) or
                self.as_of.tzinfo is None or self.as_of.utcoffset() is None):
            raise ValueError("BETA_ESTIMATE_INVALID")


@dataclass(frozen=True)
class FactorPremium:
    factor: str
    annualized_premium: Decimal
    standard_error: Decimal
    as_of: datetime
    available_at: datetime
    source: str
    quality: QualityStatus

    def __post_init__(self) -> None:
        for value in (self.as_of, self.available_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("FACTOR_PREMIUM_TIME_NOT_AWARE")
        if (not self.factor.strip() or not self.source.strip() or
                not self.annualized_premium.is_finite() or
                not self.standard_error.is_finite() or self.standard_error < 0):
            raise ValueError("FACTOR_PREMIUM_INVALID")


@dataclass(frozen=True)
class PricingResult:
    instrument_id: str
    horizon: int
    required_return: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    model_name: str
    model_key: str
    factor_loadings: Mapping[str, Decimal]
    estimation_uncertainty: Decimal
    quality_status: QualityStatus
    as_of: datetime
    validity: SignalValidity

    def __post_init__(self) -> None:
        if (not isinstance(self.validity, SignalValidity) or
                not self.instrument_id.strip() or self.horizon not in HORIZONS or
                self.validity.forecast_horizon != self.horizon or
                not self.model_name.strip() or not self.model_key.strip() or "@" not in self.model_key or
                self.as_of.tzinfo is None or
                self.as_of.utcoffset() is None or
                self.validity.valid_until <= self.as_of.astimezone(timezone.utc)):
            raise ValueError("PRICING_RESULT_INVALID")
        values = (self.required_return, self.lower_bound, self.upper_bound,
                  self.estimation_uncertainty, *self.factor_loadings.values())
        if (any(not value.is_finite() for value in values) or
                not (self.lower_bound <= self.required_return <= self.upper_bound) or
                self.estimation_uncertainty < 0):
            raise ValueError("PRICING_RESULT_INVALID")

    def _payload_without_hash(self) -> dict:
        return {
            "instrument_id": self.instrument_id, "horizon": self.horizon,
            "required_return": str(self.required_return), "lower_bound": str(self.lower_bound),
            "upper_bound": str(self.upper_bound), "model_name": self.model_name,
            "model_key": self.model_key,
            "factor_loadings": {key: str(value) for key, value in sorted(self.factor_loadings.items())},
            "estimation_uncertainty": str(self.estimation_uncertainty),
            "quality_status": str(self.quality_status),
            "as_of": self.as_of.astimezone(timezone.utc).isoformat(),
            "validity": self.validity.payload(),
        }

    @property
    def output_hash(self) -> str:
        """Canonical, version-bound identity of this model output."""
        return digest(canonical(self._payload_without_hash()))

    def payload(self) -> dict:
        return {**self._payload_without_hash(), "output_hash": self.output_hash}

    def economic_payload(self, *, currency, currency_basis, formula_version, model_key):
        if self.quality_status not in (QualityStatus.VALID, QualityStatus.ESTIMATED):
            raise DataQualityError("PRICING_SEMANTIC_QUALITY_NOT_ELIGIBLE")
        baseline = EconomicValue(ReturnSemanticType.PRICING_BASELINE_RETURN, self.required_return,
            ReturnMetricStatus.AVAILABLE, currency, currency_basis, self.horizon,
            ReturnUnit.TOTAL_RETURN, formula_version, model_key)
        body = {**self._payload_without_hash(), "schema_version": "pricing-result@2",
                "pricing_baseline_return": baseline.payload(), "model_key": model_key}
        del body['required_return']
        return body | {"output_hash": digest(canonical(body))}
