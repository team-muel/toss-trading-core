"""Point-in-time multi-factor required-return model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Mapping

from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus

from .models import FactorPremium, PricingResult
from .risk_free import annual_to_horizon


FACTORS = ("MKT", "VALUE", "MOMENTUM", "QUALITY", "SIZE", "PROFITABILITY", "INVESTMENT")


def multifactor_required_return(*, instrument_id: str, risk_free_rate: Decimal,
                                loadings: Mapping[str, Decimal],
                                premiums: Mapping[str, FactorPremium], horizon: int,
                                as_of: datetime, information_cutoff: datetime,
                                uncertainty_z: Decimal = Decimal("1.96")) -> PricingResult:
    if set(loadings) != set(FACTORS) or set(premiums) != set(FACTORS):
        raise DataQualityError("FACTOR_MODEL_INCOMPLETE")
    if any(point.factor != name or point.available_at > information_cutoff or point.as_of > as_of or
           point.quality is not QualityStatus.VALID for name, point in premiums.items()):
        raise DataQualityError("FACTOR_PREMIUM_NOT_POINT_IN_TIME")
    annual = risk_free_rate + sum(loadings[name] * premiums[name].annualized_premium for name in FACTORS)
    with localcontext() as context:
        context.prec = 34
        variance = sum((loadings[name] * premiums[name].standard_error) ** 2
                       for name in FACTORS)
        uncertainty = variance.sqrt()
    lower_annual = max(Decimal("-0.999999"), annual - uncertainty_z * uncertainty)
    upper_annual = annual + uncertainty_z * uncertainty
    return PricingResult(instrument_id, horizon, annual_to_horizon(annual, horizon),
                         annual_to_horizon(lower_annual, horizon),
                         annual_to_horizon(upper_annual, horizon), "MULTIFACTOR",
                         dict(loadings), uncertainty, QualityStatus.VALID, as_of)


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
