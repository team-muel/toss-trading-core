from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import sqlite3
from uuid import uuid4

from asset_management.domain.errors import ReconciliationError


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: str
    broker_order_id: str
    quantity_decimal: str
    amount_decimal: str
    source_response_id: str


def _exact_decimal(value: object, field: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ReconciliationError(f"invalid execution {field}") from None
    if not result.is_finite() or result < 0:
        raise ReconciliationError(f"execution {field} must be finite and non-negative")
    return result


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    execution_snapshot_id: str
    broker_order_id: str
    sequence_no: int
    cumulative_quantity: Decimal
    cumulative_amount: Decimal
    average_price: Decimal | None
    cumulative_commission: Decimal
    cumulative_tax: Decimal
    observed_at_utc: datetime
    source_response_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ExecutionDelta:
    execution_delta_id: str
    broker_order_id: str
    from_snapshot_id: str | None
    to_snapshot_id: str
    quantity: Decimal
    amount: Decimal
    commission: Decimal
    tax: Decimal

    @property
    def has_ledger_effect(self) -> bool:
        return any(value != 0 for value in (self.quantity, self.amount, self.commission, self.tax))


class ExecutionSnapshotRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def _latest(self, broker_order_id: str) -> ExecutionSnapshot | None:
        row = self._conn.execute(
            """SELECT execution_snapshot_id, broker_order_id, sequence_no,
                      cumulative_quantity_decimal, cumulative_amount_decimal,
                      average_price_decimal, cumulative_commission_decimal,
                      cumulative_tax_decimal, observed_at_utc, source_response_id,
                      content_hash
               FROM am_execution_snapshot WHERE broker_order_id=?
               ORDER BY sequence_no DESC LIMIT 1""",
            (broker_order_id,),
        ).fetchone()
        if row is None:
            return None
        return ExecutionSnapshot(
            str(row[0]), str(row[1]), int(row[2]), Decimal(row[3]), Decimal(row[4]),
            Decimal(row[5]) if row[5] is not None else None, Decimal(row[6]),
            Decimal(row[7]), datetime.fromisoformat(str(row[8])), str(row[9]), str(row[10]),
        )

    def append(
        self, *, broker_order_id: str, cumulative_quantity: object,
        cumulative_amount: object, average_price: object | None,
        cumulative_commission: object = "0", cumulative_tax: object = "0",
        observed_at_utc: datetime, source_response_id: str,
    ) -> tuple[ExecutionSnapshot, ExecutionDelta | None]:
        if observed_at_utc.tzinfo is None:
            raise ReconciliationError("execution timestamp must be timezone-aware")
        quantity = _exact_decimal(cumulative_quantity, "cumulative quantity")
        amount = _exact_decimal(cumulative_amount, "cumulative amount")
        average = _exact_decimal(average_price, "average price", optional=True)
        commission = _exact_decimal(cumulative_commission, "cumulative commission")
        tax = _exact_decimal(cumulative_tax, "cumulative tax")
        assert quantity is not None and amount is not None and commission is not None and tax is not None
        payload = {
            "quantity": _text(quantity), "amount": _text(amount), "average": _text(average),
            "commission": _text(commission), "tax": _text(tax),
        }
        content_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        duplicate = self._conn.execute(
            "SELECT execution_snapshot_id FROM am_execution_snapshot WHERE broker_order_id=? AND content_hash=?",
            (broker_order_id, content_hash),
        ).fetchone()
        if duplicate:
            latest = self._latest(broker_order_id)
            if latest is None or latest.execution_snapshot_id != duplicate[0]:
                raise ReconciliationError("repeated cumulative execution snapshot is out of order")
            return latest, None
        previous = self._latest(broker_order_id)
        previous_values = (
            (previous.cumulative_quantity, previous.cumulative_amount,
             previous.cumulative_commission, previous.cumulative_tax)
            if previous else (Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        )
        current_values = (quantity, amount, commission, tax)
        if any(current < prior for current, prior in zip(current_values, previous_values)):
            raise ReconciliationError("cumulative execution values cannot decrease")
        snapshot_id = str(uuid4())
        sequence = (previous.sequence_no + 1) if previous else 1
        instant = observed_at_utc.astimezone(timezone.utc)
        self._conn.execute(
            """INSERT INTO am_execution_snapshot
               (execution_snapshot_id, broker_order_id, sequence_no,
                cumulative_quantity_decimal, cumulative_amount_decimal,
                average_price_decimal, cumulative_commission_decimal,
                cumulative_tax_decimal, observed_at_utc, source_response_id, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, broker_order_id, sequence, _text(quantity), _text(amount),
             _text(average), _text(commission), _text(tax), instant.isoformat(),
             source_response_id, content_hash),
        )
        snapshot = ExecutionSnapshot(
            snapshot_id, broker_order_id, sequence, quantity, amount, average,
            commission, tax, instant, source_response_id, content_hash,
        )
        deltas = tuple(current - prior for current, prior in zip(current_values, previous_values))
        delta_id = str(uuid4())
        self._conn.execute(
            """INSERT INTO am_execution_delta
               (execution_delta_id, broker_order_id, from_snapshot_id, to_snapshot_id,
                quantity_decimal, amount_decimal, commission_decimal, tax_decimal,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (delta_id, broker_order_id, previous.execution_snapshot_id if previous else None,
             snapshot_id, *(_text(value) for value in deltas), instant.isoformat()),
        )
        delta = ExecutionDelta(
            delta_id, broker_order_id, previous.execution_snapshot_id if previous else None,
            snapshot_id, *deltas,
        )
        return snapshot, delta
