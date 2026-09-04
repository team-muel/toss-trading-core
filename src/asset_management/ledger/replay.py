from dataclasses import dataclass
from datetime import datetime
import sqlite3

from asset_management.ledger.cash import CashLedger, CashState
from asset_management.ledger.positions import PositionLedger, PositionState


@dataclass(frozen=True, slots=True)
class LedgerReplayResult:
    cash: tuple[CashState, ...]
    positions: tuple[PositionState, ...]


class LedgerReplay:
    """Rebuilds state solely from immutable openings, events, and reservations."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._cash = CashLedger(conn)
        self._positions = PositionLedger(conn)

    def rebuild(self, *, account_id: str, as_of_utc: datetime,
                currencies: tuple[str, ...], instruments: tuple[str, ...]) -> LedgerReplayResult:
        return LedgerReplayResult(
            tuple(self._cash.state(account_id=account_id, currency=currency,
                                   as_of_utc=as_of_utc) for currency in currencies),
            tuple(self._positions.state(account_id=account_id, instrument_id=instrument,
                                        as_of_utc=as_of_utc) for instrument in instruments),
        )
