from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import sqlite3
from uuid import uuid4

from asset_management.domain.errors import DataQualityError, ReconciliationError
from asset_management.ledger.cash import exact


@dataclass(frozen=True, slots=True)
class PositionState:
    account_id: str
    instrument_id: str
    native_currency: str
    quantity: Decimal
    average_cost: Decimal
    settled_quantity: Decimal
    unsettled_quantity: Decimal
    reserved_sell_quantity: Decimal
    available_to_sell: Decimal


class PositionLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_opening(self, *, account_id: str, instrument_id: str,
                       native_currency: str, as_of_utc: datetime, quantity: object,
                       average_cost: object, evidence: str | None,
                       approved_by: str | None, tax_policy_version: str) -> str:
        if not evidence or not approved_by:
            raise DataQualityError("OPENING_POSITION_UNKNOWN")
        if as_of_utc.tzinfo is None or not tax_policy_version:
            raise ReconciliationError("position opening timestamp must be timezone-aware")
        opening_quantity = exact(quantity, "opening quantity")
        opening_cost = exact(average_cost, "opening average cost")
        if opening_quantity < 0 or opening_cost < 0:
            raise ReconciliationError("opening position quantity and cost cannot be negative")
        opening_id = str(uuid4())
        self._conn.execute("SAVEPOINT am_position_opening")
        try:
            self._conn.execute(
                "INSERT INTO am_position_opening_balance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (opening_id, account_id, instrument_id, native_currency.upper(),
                 as_of_utc.astimezone(timezone.utc).isoformat(),
                 format(opening_quantity, "f"), format(opening_cost, "f"), evidence, approved_by),
            )
            self._conn.execute(
                """INSERT INTO am_tax_lot
                   (lot_id, execution_delta_id, account_id, instrument_id, acquisition_date,
                    settlement_date, quantity_decimal, price_decimal, commission_decimal,
                    currency, fx_rate_decimal, tax_policy_version)
                   VALUES (?, NULL, ?, ?, ?, ?, ?, ?, '0', ?, '1', ?)""",
                (f"opening:{opening_id}", account_id, instrument_id, as_of_utc.date().isoformat(),
                 as_of_utc.date().isoformat(), format(opening_quantity, "f"),
                 format(opening_cost, "f"),
                 native_currency.upper(), tax_policy_version),
            )
            self._conn.execute("RELEASE SAVEPOINT am_position_opening")
        except Exception:
            self._conn.execute("ROLLBACK TO SAVEPOINT am_position_opening")
            self._conn.execute("RELEASE SAVEPOINT am_position_opening")
            raise
        return opening_id

    def reserve_sell(self, *, broker_order_id: str, account_id: str,
                     instrument_id: str, quantity: object, source_response_id: str,
                     observed_at_utc: datetime, released: bool = False) -> str:
        if observed_at_utc.tzinfo is None:
            raise ReconciliationError("position reservation timestamp must be timezone-aware")
        value = exact(quantity, "reserved sell quantity")
        if value < 0:
            raise ReconciliationError("reserved sell quantity cannot be negative")
        existing = self._conn.execute(
            "SELECT reservation_event_id FROM am_position_reservation_event WHERE broker_order_id=? AND source_response_id=?",
            (broker_order_id, source_response_id),
        ).fetchone()
        if existing:
            return str(existing[0])
        sequence = self._conn.execute(
            "SELECT COALESCE(MAX(sequence_no),0)+1 FROM am_position_reservation_event WHERE broker_order_id=?",
            (broker_order_id,),
        ).fetchone()[0]
        event_id = str(uuid4())
        self._conn.execute(
            "INSERT INTO am_position_reservation_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, broker_order_id, sequence, account_id, instrument_id,
             "0" if released else format(value, "f"),
             "RELEASED" if released else "RESERVED", source_response_id,
             observed_at_utc.astimezone(timezone.utc).isoformat()),
        )
        return event_id

    def state(self, *, account_id: str, instrument_id: str, as_of_utc: datetime,
              broker_sellable_constraint: object | None = None) -> PositionState:
        if as_of_utc.tzinfo is None:
            raise ReconciliationError("position state timestamp must be timezone-aware")
        as_of = as_of_utc.astimezone(timezone.utc)
        opening = self._conn.execute(
            """SELECT native_currency, quantity_decimal, average_cost_decimal, as_of_utc
               FROM am_position_opening_balance WHERE account_id=? AND instrument_id=?""",
            (account_id, instrument_id),
        ).fetchone()
        if opening is None or datetime.fromisoformat(opening[3]) > as_of:
            raise DataQualityError("OPENING_POSITION_UNKNOWN")
        total = settled = Decimal(opening[1])
        for quantity, settlement in self._conn.execute(
            """SELECT p.quantity_delta_decimal, s.settlement_date
               FROM am_position_ledger p LEFT JOIN am_position_event_settlement s
                 ON s.position_event_id=p.position_event_id
               WHERE p.account_id=? AND p.instrument_id=?
                 AND p.created_at_utc>=? AND p.created_at_utc<=?""",
            (account_id, instrument_id, opening[3], as_of.isoformat()),
        ):
            value = Decimal(quantity)
            total += value
            if settlement is None or date.fromisoformat(settlement) <= as_of.date():
                settled += value
        reserved_rows = self._conn.execute(
            """WITH latest AS (SELECT broker_order_id, MAX(sequence_no) seq
                 FROM am_position_reservation_event WHERE account_id=? AND instrument_id=?
                 AND observed_at_utc>=? AND observed_at_utc<=? GROUP BY broker_order_id)
               SELECT e.reserved_quantity_decimal
               FROM latest l JOIN am_position_reservation_event e
               ON e.broker_order_id=l.broker_order_id AND e.sequence_no=l.seq
               WHERE e.status='RESERVED'""",
            (account_id, instrument_id, opening[3], as_of.isoformat()),
        )
        reserved = sum((Decimal(row[0]) for row in reserved_rows), Decimal(0))
        if total < 0 or settled < 0:
            raise ReconciliationError("position quantity cannot be negative")
        available = settled - reserved
        if available < 0:
            raise ReconciliationError("reserved sell quantity exceeds settled position")
        if broker_sellable_constraint is not None:
            available = min(available, exact(broker_sellable_constraint, "broker sellable quantity"))
        from asset_management.ledger.tax_lots import TaxLotLedger
        lots = TaxLotLedger(self._conn).lots(account_id=account_id, instrument_id=instrument_id)
        remaining = [lot for lot in lots if lot.remaining_quantity > 0]
        lot_quantity = sum((lot.remaining_quantity for lot in remaining), Decimal(0))
        average_cost = (
            sum((lot.remaining_quantity * lot.price + lot.commission
                 for lot in remaining), Decimal(0)) / lot_quantity
            if lot_quantity > 0 else Decimal(0)
        )
        return PositionState(account_id, instrument_id, opening[0], total,
                             average_cost, settled, total - settled,
                             reserved, available)
