from datetime import date
from typing import Protocol


class TradingCalendar(Protocol):
    version: str

    def is_session(self, day: date, market: str) -> bool: ...
