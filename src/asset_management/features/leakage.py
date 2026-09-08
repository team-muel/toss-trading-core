"""Point-in-time standardization and cross-sectional transforms."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Mapping, Sequence

from asset_management.domain.errors import DataQualityError


@dataclass(frozen=True)
class DatedValue:
    value: Decimal
    event_time: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        for instant in (self.event_time, self.available_at):
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("FEATURE_INPUT_TIME_NOT_AWARE")


def eligible_history(values: Sequence[DatedValue], *, as_of: datetime,
                     information_cutoff: datetime) -> tuple[Decimal, ...]:
    if any(value.tzinfo is None or value.utcoffset() is None
           for value in (as_of, information_cutoff)):
        raise ValueError("FEATURE_CONTEXT_TIME_NOT_AWARE")
    return tuple(item.value for item in values
                 if item.event_time.astimezone(timezone.utc) < as_of.astimezone(timezone.utc)
                 and item.available_at.astimezone(timezone.utc) <= information_cutoff.astimezone(timezone.utc))


def historical_zscore(value: object, history: Sequence[DatedValue], *, as_of: datetime,
                      information_cutoff: datetime, minimum_history: int) -> Decimal:
    selected = eligible_history(history, as_of=as_of, information_cutoff=information_cutoff)
    if len(selected) < minimum_history:
        raise DataQualityError("MISSING_HISTORY")
    mean = sum(selected) / Decimal(len(selected))
    variance = sum((item - mean) ** 2 for item in selected) / Decimal(len(selected))
    if variance == 0:
        raise DataQualityError("FEATURE_VARIANCE_ZERO")
    with localcontext() as context:
        context.prec = 34
        try:
            return (Decimal(str(value)) - mean) / variance.sqrt()
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise DataQualityError("FEATURE_INPUT_INVALID") from exc


def cross_sectional_winsorize(values: Mapping[str, object], *, historical_universe: Sequence[str],
                              lower_fraction: Decimal = Decimal("0.05"),
                              upper_fraction: Decimal = Decimal("0.95")) -> dict[str, Decimal]:
    universe = tuple(historical_universe)
    if len(universe) != len(set(universe)) or set(values) != set(universe):
        raise DataQualityError("HISTORICAL_UNIVERSE_MISMATCH")
    if not Decimal(0) <= lower_fraction <= upper_fraction <= Decimal(1):
        raise ValueError("WINSORIZATION_POLICY_INVALID")
    try:
        ordered = sorted(Decimal(str(values[item])) for item in universe)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataQualityError("FEATURE_INPUT_INVALID") from exc
    if not ordered:
        raise DataQualityError("MISSING_HISTORY")
    last = len(ordered) - 1
    lower = ordered[int(lower_fraction * last)]
    upper = ordered[int(upper_fraction * last)]
    return {item: min(upper, max(lower, Decimal(str(values[item])))) for item in universe}
