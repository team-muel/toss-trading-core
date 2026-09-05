"""Point-in-time risk-free curves and exact horizon compounding."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Iterable

from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus

from .models import HORIZONS, RiskFreePoint


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
