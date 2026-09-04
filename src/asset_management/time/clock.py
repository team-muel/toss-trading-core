from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> datetime: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("fixed instant must be timezone-aware")
        self._instant = instant.astimezone(timezone.utc)

    def now_utc(self) -> datetime:
        return self._instant
