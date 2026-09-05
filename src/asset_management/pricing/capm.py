"""CAPM required return with estimated and shrinkable beta."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Sequence

from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus

from .models import BetaEstimate, PricingResult
from .risk_free import annual_to_horizon


def estimate_beta(asset_returns: Sequence[Decimal], market_returns: Sequence[Decimal], *,
                  as_of: datetime, minimum_observations: int = 60,
                  instability_standard_error: Decimal = Decimal("0.25"),
                  prior_beta: Decimal = Decimal(1)) -> BetaEstimate:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise DataQualityError("BETA_AS_OF_NOT_AWARE")
    if len(asset_returns) != len(market_returns) or len(asset_returns) < minimum_observations:
        raise DataQualityError("BETA_HISTORY_MISSING")
    if minimum_observations < 3 or instability_standard_error <= 0:
        raise ValueError("BETA_POLICY_INVALID")
    n = len(asset_returns)
    if any(not value.is_finite() for value in (*asset_returns, *market_returns)):
        raise DataQualityError("BETA_INPUT_INVALID")
    x_mean = sum(market_returns) / Decimal(n)
    y_mean = sum(asset_returns) / Decimal(n)
    sxx = sum((x - x_mean) ** 2 for x in market_returns)
    if sxx == 0:
        raise DataQualityError("BETA_MARKET_VARIANCE_ZERO")
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(market_returns, asset_returns))
    raw = sxy / sxx
    alpha = y_mean - raw * x_mean
    residuals = tuple(y - alpha - raw * x for x, y in zip(market_returns, asset_returns))
    sse = sum(value * value for value in residuals)
    syy = sum((y - y_mean) ** 2 for y in asset_returns)
    r_squared = Decimal(1) - sse / syy if syy else Decimal(0)
    with localcontext() as context:
        context.prec = 34
        standard_error = (sse / Decimal(n - 2) / sxx).sqrt()
    reliability = Decimal(1) / (Decimal(1) + (standard_error / instability_standard_error) ** 2)
    beta = reliability * raw + (Decimal(1) - reliability) * prior_beta
    quality = QualityStatus.ESTIMATED if reliability < Decimal("0.8") else QualityStatus.VALID
    return BetaEstimate(beta, raw, standard_error, n, n, r_squared, as_of, quality, reliability)


def capm_required_return(*, instrument_id: str, risk_free_rate: Decimal,
                         beta: BetaEstimate, market_risk_premium: Decimal,
                         horizon: int, as_of: datetime,
                         uncertainty_z: Decimal = Decimal("1.96")) -> PricingResult:
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
    return PricingResult(
        instrument_id, horizon, annual_to_horizon(annual, horizon),
        annual_to_horizon(lower_annual, horizon), annual_to_horizon(upper_annual, horizon),
        "CAPM", {"MKT": beta.beta}, annual_uncertainty, beta.quality, as_of,
    )
