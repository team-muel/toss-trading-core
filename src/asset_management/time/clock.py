from datetime import datetime, timezone
from typing import Protocol

from asset_management.domain.errors import TemporalViolation


class Clock(Protocol):
    def now_utc(self) -> datetime: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise TemporalViolation("frozen instant must be timezone-aware")
        self._instant = instant.astimezone(timezone.utc)

    def now_utc(self) -> datetime:
        return self._instant


class ReplayClock:
    """Explicit monotonic clock for deterministic historical event replay."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise TemporalViolation("replay instant must be timezone-aware")
        self._instant = instant.astimezone(timezone.utc)

    def now_utc(self) -> datetime:
        return self._instant

    def advance_to(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise TemporalViolation("replay instant must be timezone-aware")
        candidate = instant.astimezone(timezone.utc)
        if candidate < self._instant:
            raise TemporalViolation("replay clock cannot move backwards")
        self._instant = candidate


FixedClock = FrozenClock
