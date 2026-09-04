from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from asset_management.account.executions import ExecutionDelta
from asset_management.domain.errors import ReconciliationError


@dataclass(frozen=True, slots=True)
class ExecutionPostingContext:
    account_id: str
    instrument_id: str
    side: str
    currency: str
    settlement_date: date | None = None

    def __post_init__(self) -> None:
        side = self.side.strip().upper()
        if side not in {"BUY", "SELL"}:
            raise ReconciliationError(f"unsupported execution side: {self.side!r}")
        if not self.account_id or not self.instrument_id or not self.currency:
            raise ReconciliationError("execution posting context is incomplete")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "currency", self.currency.strip().upper())


@dataclass(frozen=True, slots=True)
class ExecutionPosting:
    execution_delta_id: str
    cash_event_id: str | None
    position_event_id: str | None
    cash_delta: Decimal
    quantity_delta: Decimal


class ExecutionLedgerPoster:
    """Posts each immutable execution delta to cash and positions exactly once."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def post(
        self, delta: ExecutionDelta, context: ExecutionPostingContext,
        *, posted_at_utc: datetime,
    ) -> ExecutionPosting:
        if posted_at_utc.tzinfo is None:
            raise ReconciliationError("posting timestamp must be timezone-aware")
        existing = self._conn.execute(
            "SELECT cash_event_id, position_event_id FROM am_execution_posting WHERE execution_delta_id=?",
            (delta.execution_delta_id,),
        ).fetchone()
        direction = Decimal("1") if context.side == "BUY" else Decimal("-1")
        quantity_delta = direction * delta.quantity
        principal_direction = Decimal("-1") if context.side == "BUY" else Decimal("1")
        cash_delta = principal_direction * delta.amount - delta.commission - delta.tax
        if existing:
            return ExecutionPosting(
                delta.execution_delta_id, existing[0], existing[1], cash_delta, quantity_delta
            )

        cash_event_id = (
            str(uuid5(NAMESPACE_URL, f"cash:{delta.execution_delta_id}"))
            if cash_delta != 0 else None
        )
        position_event_id = (
            str(uuid5(NAMESPACE_URL, f"position:{delta.execution_delta_id}"))
            if quantity_delta != 0 else None
        )
        instant = posted_at_utc.astimezone(timezone.utc).isoformat()
        self._conn.execute("SAVEPOINT am_execution_posting")
        try:
            if cash_event_id:
                self._conn.execute(
                    """INSERT INTO am_cash_ledger
                       (cash_event_id, execution_id, account_id, currency,
                        amount_decimal, settlement_date, event_type, created_at_utc)
                       VALUES (?, NULL, ?, ?, ?, ?, ?, ?)""",
                    (cash_event_id, context.account_id, context.currency,
                     format(cash_delta, "f"),
                     context.settlement_date.isoformat() if context.settlement_date else None,
                     f"EXECUTION_{context.side}", instant),
                )
            if position_event_id:
                self._conn.execute(
                    """INSERT INTO am_position_ledger
                       (position_event_id, execution_id, account_id, instrument_id,
                        quantity_delta_decimal, event_type, created_at_utc)
                       VALUES (?, NULL, ?, ?, ?, ?, ?)""",
                    (position_event_id, context.account_id, context.instrument_id,
                     format(quantity_delta, "f"), f"EXECUTION_{context.side}", instant),
                )
            self._conn.execute(
                "INSERT INTO am_execution_posting VALUES (?, ?, ?, ?)",
                (delta.execution_delta_id, cash_event_id, position_event_id, instant),
            )
            self._conn.execute("RELEASE SAVEPOINT am_execution_posting")
        except Exception:
            self._conn.execute("ROLLBACK TO SAVEPOINT am_execution_posting")
            self._conn.execute("RELEASE SAVEPOINT am_execution_posting")
            raise
        return ExecutionPosting(
            delta.execution_delta_id, cash_event_id, position_event_id,
            cash_delta, quantity_delta,
        )
