"""Point-in-time risk-free curves and exact horizon compounding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Iterable

from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus

from .models import HORIZONS, RiskFreePoint


@dataclass(frozen=True)
class RiskFreeReturn:
    """A risk-free input bound to one currency and decision horizon."""

    currency: str
    forecast_horizon: int
    annualized_rate: Decimal
    holding_period_risk_free_return: Decimal
    day_count: str
    compounding: str
    as_of: datetime
    formula_version: str
    source: str
    quality: QualityStatus
    uncertainty: Decimal

    def __post_init__(self) -> None:
        if (not self.currency.strip() or self.forecast_horizon not in HORIZONS or
                self.day_count != "BUS/252" or self.compounding != "EFFECTIVE_ANNUAL" or
                not self.formula_version.strip() or not self.source.strip() or
                self.as_of.tzinfo is None or self.as_of.utcoffset() is None or
                any(not item.is_finite() for item in (
                    self.annualized_rate, self.holding_period_risk_free_return, self.uncertainty)) or
                self.annualized_rate <= Decimal(-1) or self.uncertainty < 0 or
                self.quality is not QualityStatus.VALID):
            raise DataQualityError("RISK_FREE_RETURN_INVALID")
        if self.holding_period_risk_free_return != annual_to_horizon(
                self.annualized_rate, self.forecast_horizon):
            raise DataQualityError("RISK_FREE_RETURN_FORMULA_CONFLICT")

    def payload(self) -> dict[str, object]:
        return {"currency": self.currency, "forecast_horizon": self.forecast_horizon,
                "annualized_rate": str(self.annualized_rate),
                "holding_period_risk_free_return": str(self.holding_period_risk_free_return),
                "day_count": self.day_count, "compounding": self.compounding,
                "as_of": self.as_of.astimezone(timezone.utc).isoformat(),
                "formula_version": self.formula_version, "source": self.source,
                "quality": self.quality.value, "uncertainty": str(self.uncertainty)}


def annual_to_horizon(annual_return: Decimal, horizon_days: int) -> Decimal:
    if horizon_days not in HORIZONS or not annual_return.is_finite() or annual_return <= Decimal(-1):
        raise DataQualityError("HORIZON_CONVERSION_INPUT_INVALID")
    with localcontext() as context:
        context.prec = 34
        try:
            return ((Decimal(1) + annual_return).ln() * Decimal(horizon_days) / Decimal(252)).exp() - 1
        except InvalidOperation as exc:
            raise DataQualityError("HORIZON_CONVERSION_FAILED") from exc


class RiskFreeCurve:
    def __init__(self, points: Iterable[RiskFreePoint]):
        values = tuple(points)
        if {item.horizon for item in values} != set(HORIZONS) or len(values) != len(HORIZONS):
            raise DataQualityError("RISK_FREE_CURVE_INCOMPLETE")
        if len({item.as_of.astimezone(timezone.utc) for item in values}) != 1:
            raise DataQualityError("RISK_FREE_CURVE_AS_OF_CONFLICT")
        if len({item.currency for item in values}) != 1:
            raise DataQualityError("RISK_FREE_CURVE_CURRENCY_CONFLICT")
        if len({(item.day_count, item.compounding, item.formula_version) for item in values}) != 1:
            raise DataQualityError("RISK_FREE_CURVE_CONVENTION_CONFLICT")
        self.points = {item.horizon: item for item in values}

    def rate(self, *, horizon: int, information_cutoff: datetime) -> Decimal:
        if information_cutoff.tzinfo is None or information_cutoff.utcoffset() is None:
            raise DataQualityError("PRICING_CUTOFF_NOT_AWARE")
        try:
            point = self.points[horizon]
        except KeyError:
            raise DataQualityError("RISK_FREE_HORIZON_MISSING") from None
        if point.as_of > information_cutoff or point.quality is not QualityStatus.VALID:
            raise DataQualityError("RISK_FREE_POINT_NOT_ELIGIBLE")
        return point.annualized_rate

    def return_for(self, *, currency: str, horizon: int,
                   information_cutoff: datetime) -> RiskFreeReturn:
        if not currency.strip():
            raise DataQualityError("RISK_FREE_CURRENCY_INVALID")
        try:
            point = self.points[horizon]
        except KeyError:
            raise DataQualityError("RISK_FREE_HORIZON_MISSING") from None
        if point.currency != currency:
            raise DataQualityError("RISK_FREE_CURRENCY_MISMATCH")
        self.rate(horizon=horizon, information_cutoff=information_cutoff)
        return RiskFreeReturn(point.currency, point.horizon, point.annualized_rate,
            annual_to_horizon(point.annualized_rate, point.horizon), point.day_count,
            point.compounding, point.as_of, point.formula_version, point.source,
            point.quality, point.uncertainty)


def require_risk_free_alignment(*, risk_free: RiskFreeReturn, currency: str,
                                forecast_horizon: int, information_cutoff: datetime) -> None:
    if information_cutoff.tzinfo is None or information_cutoff.utcoffset() is None:
        raise DataQualityError("PRICING_CUTOFF_NOT_AWARE")
    if risk_free.currency != currency:
        raise DataQualityError("RISK_FREE_CURRENCY_MISMATCH")
    if risk_free.forecast_horizon != forecast_horizon:
        raise DataQualityError("RISK_FREE_HORIZON_MISMATCH")
    if risk_free.as_of > information_cutoff:
        raise DataQualityError("RISK_FREE_POINT_NOT_ELIGIBLE")
