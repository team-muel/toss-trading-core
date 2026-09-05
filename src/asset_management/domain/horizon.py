"""Decision horizon, signal validity, and decay contracts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Iterable

from .errors import DataQualityError, InvariantViolation


DECISION_HORIZONS = (21, 63, 126, 252)


class DecayProfile(StrEnum):
    STEP = "STEP"
    LINEAR = "LINEAR"
    EXPONENTIAL = "EXPONENTIAL"


@dataclass(frozen=True, slots=True)
class SignalValidity:
    forecast_horizon: int
    holding_horizon: int
    valid_until: datetime
    decay_profile: DecayProfile
    half_life_seconds: int | None = None

    def __post_init__(self) -> None:
        if (self.forecast_horizon not in DECISION_HORIZONS or
                self.holding_horizon not in DECISION_HORIZONS):
            raise InvariantViolation("SIGNAL_HORIZON_UNSUPPORTED")
        if self.valid_until.tzinfo is None or self.valid_until.utcoffset() is None:
            raise InvariantViolation("SIGNAL_VALID_UNTIL_NOT_AWARE")
        if not isinstance(self.decay_profile, DecayProfile):
            raise InvariantViolation("SIGNAL_DECAY_PROFILE_UNKNOWN")
        if ((self.decay_profile is DecayProfile.EXPONENTIAL and
             (type(self.half_life_seconds) is not int or self.half_life_seconds <= 0)) or
                (self.decay_profile is not DecayProfile.EXPONENTIAL and
                 self.half_life_seconds is not None)):
            raise InvariantViolation("SIGNAL_DECAY_POLICY_INVALID")
        object.__setattr__(self, "valid_until", self.valid_until.astimezone(timezone.utc))

    def effective_weight(self, *, produced_at: datetime, evaluated_at: datetime) -> Decimal:
        for value in (produced_at, evaluated_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise DataQualityError("SIGNAL_EVALUATION_TIME_NOT_AWARE")
        produced = produced_at.astimezone(timezone.utc)
        evaluated = evaluated_at.astimezone(timezone.utc)
        if self.valid_until <= produced:
            raise InvariantViolation("SIGNAL_VALIDITY_WINDOW_INVALID")
        if evaluated < produced:
            raise DataQualityError("SIGNAL_EVALUATED_BEFORE_PRODUCTION")
        if evaluated > self.valid_until:
            return Decimal(0)
        if self.decay_profile is DecayProfile.STEP:
            return Decimal(1)
        elapsed = Decimal(str((evaluated - produced).total_seconds()))
        duration = Decimal(str((self.valid_until - produced).total_seconds()))
        if self.decay_profile is DecayProfile.LINEAR:
            return (duration - elapsed) / duration
        with localcontext() as context:
            context.prec = 34
            half_life = Decimal(self.half_life_seconds)
            return (-(Decimal(2).ln()) * elapsed / half_life).exp()

    def payload(self) -> dict[str, object]:
        return {
            "forecast_horizon": self.forecast_horizon,
            "holding_horizon": self.holding_horizon,
            "valid_until": self.valid_until.isoformat(),
            "decay_profile": self.decay_profile.value,
            "half_life_seconds": self.half_life_seconds,
        }


def require_horizon_alignment(signals: Iterable[SignalValidity]) -> SignalValidity:
    values = tuple(signals)
    if not values:
        raise DataQualityError("SIGNAL_SET_EMPTY")
    first = values[0]
    if any((item.forecast_horizon, item.holding_horizon) !=
           (first.forecast_horizon, first.holding_horizon) for item in values[1:]):
        raise DataQualityError("SIGNAL_HORIZON_MISMATCH")
    return first
