"""CAPM required return with point-in-time beta estimation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import re
from typing import Sequence

from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus
from asset_management.domain.horizon import SignalValidity
from asset_management.governance import ModelAuthorization, ModelRegistry, ModelScope

from .models import BetaEstimate, PricingResult
from .risk_free import annual_to_horizon


_HASH = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ReturnObservation:
    value: Decimal
    event_time: datetime
    available_at: datetime
    manifest_id: str

    def __post_init__(self) -> None:
        if (not isinstance(self.value, Decimal) or not self.value.is_finite() or
                not isinstance(self.event_time, datetime) or self.event_time.tzinfo is None or
                self.event_time.utcoffset() is None or not isinstance(self.available_at, datetime) or
                self.available_at.tzinfo is None or self.available_at.utcoffset() is None or
                not isinstance(self.manifest_id, str) or _HASH.fullmatch(self.manifest_id) is None):
            raise DataQualityError("BETA_OBSERVATION_INVALID")
        event = self.event_time.astimezone(timezone.utc)
        available = self.available_at.astimezone(timezone.utc)
        if available < event:
            raise DataQualityError("BETA_OBSERVATION_AVAILABILITY_INVALID")
        object.__setattr__(self, "event_time", event)
        object.__setattr__(self, "available_at", available)


def estimate_beta(asset_returns: Sequence[ReturnObservation], market_returns: Sequence[ReturnObservation], *,
                  as_of: datetime, minimum_observations: int = 60,
                  instability_standard_error: Decimal = Decimal("0.25"),
                  prior_beta: Decimal = Decimal(1)) -> BetaEstimate:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise DataQualityError("BETA_AS_OF_NOT_AWARE")
    instant = as_of.astimezone(timezone.utc)
    if len(asset_returns) != len(market_returns) or len(asset_returns) < minimum_observations:
        raise DataQualityError("BETA_HISTORY_MISSING")
    if minimum_observations < 3 or instability_standard_error <= 0:
        raise ValueError("BETA_POLICY_INVALID")
    if (any(not isinstance(item, ReturnObservation) for item in (*asset_returns, *market_returns)) or
            any(item.event_time > instant or item.available_at > instant for item in (*asset_returns, *market_returns)) or
            any(asset.event_time != market.event_time for asset, market in zip(asset_returns, market_returns))):
        raise DataQualityError("BETA_PIT_EVIDENCE_INVALID")
    event_times = tuple(item.event_time for item in asset_returns)
    if len(set(event_times)) != len(event_times):
        raise DataQualityError("BETA_DUPLICATE_OBSERVATION")
    asset_values = tuple(item.value for item in asset_returns)
    market_values = tuple(item.value for item in market_returns)
    n = len(asset_values)
    x_mean = sum(market_values) / Decimal(n)
    y_mean = sum(asset_values) / Decimal(n)
    sxx = sum((x - x_mean) ** 2 for x in market_values)
    if sxx == 0:
        raise DataQualityError("BETA_MARKET_VARIANCE_ZERO")
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(market_values, asset_values))
    raw = sxy / sxx
    alpha = y_mean - raw * x_mean
    residuals = tuple(y - alpha - raw * x for x, y in zip(market_values, asset_values))
    sse = sum(value * value for value in residuals)
    syy = sum((y - y_mean) ** 2 for y in asset_values)
    r_squared = Decimal(1) - sse / syy if syy else Decimal(0)
    with localcontext() as context:
        context.prec = 34
        standard_error = (sse / Decimal(n - 2) / sxx).sqrt()
    reliability = Decimal(1) / (Decimal(1) + (standard_error / instability_standard_error) ** 2)
    beta = reliability * raw + (Decimal(1) - reliability) * prior_beta
    quality = QualityStatus.ESTIMATED if reliability < Decimal("0.8") else QualityStatus.VALID
    return BetaEstimate(beta, raw, standard_error, n, n, r_squared, instant, quality, reliability)


def capm_required_return(*, instrument_id: str, risk_free_rate: Decimal,
                         beta: BetaEstimate, market_risk_premium: Decimal,
                         horizon: int, as_of: datetime,
                         validity: SignalValidity,
                         model_registry: ModelRegistry,
                         authorization: ModelAuthorization,
                         uncertainty_z: Decimal = Decimal("1.96")) -> PricingResult:
    model_registry.require_authorization(
        authorization, model_key="CAPM@1", scope=ModelScope.REQUIRED_RETURN, at=as_of)
    return _capm_numeric(instrument_id=instrument_id, risk_free_rate=risk_free_rate, beta=beta,
                         market_risk_premium=market_risk_premium, horizon=horizon, as_of=as_of,
                         validity=validity, uncertainty_z=uncertainty_z)


def _capm_numeric(*, instrument_id, risk_free_rate, beta, market_risk_premium, horizon,
                  as_of, validity, uncertainty_z=Decimal("1.96")):
    if (as_of.tzinfo is None or as_of.utcoffset() is None or
            not risk_free_rate.is_finite() or not market_risk_premium.is_finite() or
            uncertainty_z < 0):
        raise DataQualityError("CAPM_INPUT_INVALID")
    if beta.as_of > as_of or beta.quality in {QualityStatus.MISSING, QualityStatus.BLOCKED,
                                             QualityStatus.CONFLICT, QualityStatus.QUARANTINED}:
        raise DataQualityError("BETA_NOT_ELIGIBLE")
    annual = risk_free_rate + beta.beta * market_risk_premium
    annual_uncertainty = abs(market_risk_premium) * beta.standard_error
    lower_annual = max(Decimal("-0.999999"), annual - uncertainty_z * annual_uncertainty)
    upper_annual = annual + uncertainty_z * annual_uncertainty
    point = annual_to_horizon(annual, horizon)
    lower = annual_to_horizon(lower_annual, horizon)
    upper = annual_to_horizon(upper_annual, horizon)
    horizon_uncertainty = max(point-lower, upper-point) / uncertainty_z if uncertainty_z else Decimal(0)
    return PricingResult(
        instrument_id, horizon, point, lower, upper,
        "CAPM", "CAPM@1", {"MKT": beta.beta}, horizon_uncertainty, beta.quality, as_of,
        validity,
    )


def capm_pricing_baseline_return(*, currency, currency_basis, asset_scope,
                                 model_registry, authorization, **inputs):
    """Canonical v2 output; legacy REQUIRED_RETURN authority is insufficient."""
    if asset_scope not in ("EQUITY", "EQUITY_ETF"):
        raise DataQualityError("PRICING_ASSET_SCOPE_NOT_APPLICABLE")
    model_registry.require_authorization(authorization, model_key="CAPM@2",
        scope=ModelScope.PRICING_BASELINE_RETURN, at=inputs['as_of'])
    result = _capm_numeric(**inputs)
    return result.economic_payload(currency=currency, currency_basis=currency_basis,
        formula_version="capm-pricing-baseline@2", model_key="CAPM@2")
