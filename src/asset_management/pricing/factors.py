"""Point-in-time multi-factor required-return model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping

from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus

from .models import FactorPremium, PricingResult
from .risk_free import annual_to_horizon
from asset_management.domain.horizon import SignalValidity
from asset_management.governance import ModelAuthorization, ModelRegistry, ModelScope


FACTORS = ("MKT", "VALUE", "MOMENTUM", "QUALITY", "SIZE", "PROFITABILITY", "INVESTMENT")


def multifactor_required_return(*, instrument_id: str, risk_free_rate: Decimal,
                                loadings: Mapping[str, Decimal],
                                premiums: Mapping[str, FactorPremium], horizon: int,
                                as_of: datetime, information_cutoff: datetime, validity: SignalValidity,
                                model_registry: ModelRegistry, authorization: ModelAuthorization,
                                uncertainty_z: Decimal = Decimal("1.96")) -> PricingResult:
    model_registry.require_authorization(
        authorization, model_key="MULTIFACTOR@1", scope=ModelScope.REQUIRED_RETURN, at=as_of)
    if set(loadings) != set(FACTORS) or set(premiums) != set(FACTORS):
        raise DataQualityError("FACTOR_MODEL_INCOMPLETE")
    if any(point.factor != name or point.available_at > information_cutoff or point.as_of > as_of or
           point.quality is not QualityStatus.VALID for name, point in premiums.items()):
        raise DataQualityError("FACTOR_PREMIUM_NOT_POINT_IN_TIME")
    annual = risk_free_rate + sum(loadings[name] * premiums[name].annualized_premium for name in FACTORS)
    # No error-covariance matrix is supplied, so use the conservative triangle bound.
    uncertainty = sum(abs(loadings[name]) * premiums[name].standard_error for name in FACTORS)
    lower_annual = max(Decimal("-0.999999"), annual - uncertainty_z * uncertainty)
    upper_annual = annual + uncertainty_z * uncertainty
    point=annual_to_horizon(annual,horizon); lower=annual_to_horizon(lower_annual,horizon)
    upper=annual_to_horizon(upper_annual,horizon)
    horizon_uncertainty=max(point-lower,upper-point)/uncertainty_z if uncertainty_z else Decimal(0)
    return PricingResult(instrument_id,horizon,point,lower,upper,"MULTIFACTOR",
                         dict(loadings),horizon_uncertainty,QualityStatus.VALID,as_of,validity)


def require_distinct_factor_roles(*, required_return_factors: set[str],
                                  expected_return_overlay_factors: set[str]) -> None:
    if required_return_factors & expected_return_overlay_factors:
        raise DataQualityError("FACTOR_DOUBLE_COUNTING")


def require_separate_timing_overlay(*, long_horizon_premium: Decimal,
                                    short_horizon_signal: Decimal) -> tuple[Decimal, Decimal]:
    """Return separate components; callers may not add them as interchangeable premia."""
    if not long_horizon_premium.is_finite() or not short_horizon_signal.is_finite():
        raise DataQualityError("RETURN_OVERLAY_INVALID")
    return long_horizon_premium, short_horizon_signal
