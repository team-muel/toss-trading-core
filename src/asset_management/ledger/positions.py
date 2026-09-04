from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import sqlite3
from uuid import uuid4

from asset_management.domain.errors import DataQualityError, ReconciliationError
from asset_management.ledger.cash import BrokerConstraint, exact


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
        evidence_exists = self._conn.execute(
            """SELECT 1 FROM am_raw_api_response WHERE raw_response_id=?
               UNION ALL SELECT 1 FROM am_dataset_manifest WHERE dataset_manifest_id=? LIMIT 1""",
            (evidence, evidence),
        ).fetchone()
        if evidence_exists is None:
            raise DataQualityError("OPENING_POSITION_EVIDENCE_MISSING")
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
            self._conn.execute(
                "INSERT INTO am_tax_lot_timing VALUES (?, ?)",
                (f"opening:{opening_id}", as_of_utc.astimezone(timezone.utc).isoformat()),
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
        instant = observed_at_utc.astimezone(timezone.utc)
        reserved = Decimal(0) if released else value
        status = "RELEASED" if released else "RESERVED"
        existing = self._conn.execute(
            """SELECT reservation_event_id, account_id, instrument_id,
                      reserved_quantity_decimal, status, observed_at_utc
               FROM am_position_reservation_event
               WHERE broker_order_id=? AND source_response_id=?""",
            (broker_order_id, source_response_id),
        ).fetchone()
        if existing:
            persisted = (
                str(existing[1]), str(existing[2]), Decimal(existing[3]),
                str(existing[4]), datetime.fromisoformat(str(existing[5])),
            )
            requested = (account_id, instrument_id, reserved, status, instant)
            if requested != persisted:
                raise ReconciliationError("reservation evidence conflicts with existing position reservation")
            return str(existing[0])
        latest = self._conn.execute(
            """SELECT sequence_no, account_id, instrument_id, observed_at_utc
               FROM am_position_reservation_event WHERE broker_order_id=?
               ORDER BY sequence_no DESC LIMIT 1""",
            (broker_order_id,),
        ).fetchone()
        if latest is not None:
            if (str(latest[1]), str(latest[2])) != (account_id, instrument_id):
                raise ReconciliationError("position reservation identity cannot change")
            if instant < datetime.fromisoformat(str(latest[3])):
                raise ReconciliationError("position reservation time cannot move backwards")
        sequence = int(latest[0]) + 1 if latest is not None else 1
        event_id = str(uuid4())
        self._conn.execute(
            "INSERT INTO am_position_reservation_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, broker_order_id, sequence, account_id, instrument_id,
             format(reserved, "f"), status, source_response_id, instant.isoformat()),
        )
        return event_id

    def state(self, *, account_id: str, instrument_id: str, as_of_utc: datetime,
              broker_sellable_constraint: BrokerConstraint | None = None) -> PositionState:
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
        missing_timing = self._conn.execute(
            """SELECT COUNT(*) FROM am_tax_lot l
               LEFT JOIN am_tax_lot_timing t USING(lot_id)
               WHERE l.account_id=? AND l.instrument_id=? AND t.lot_id IS NULL""",
            (account_id, instrument_id),
        ).fetchone()[0]
        missing_disposal_timing = self._conn.execute(
            """SELECT COUNT(*) FROM am_tax_lot_disposal d
               JOIN am_tax_lot l USING(lot_id)
               LEFT JOIN am_tax_lot_disposal_timing t USING(disposal_id)
               WHERE l.account_id=? AND l.instrument_id=? AND t.disposal_id IS NULL""",
            (account_id, instrument_id),
        ).fetchone()[0]
        if missing_timing or missing_disposal_timing:
            raise ReconciliationError("tax-lot point-in-time evidence is missing")
        total = settled = Decimal(opening[1])
        for quantity, settlement_record, settlement in self._conn.execute(
            """SELECT p.quantity_delta_decimal, s.position_event_id, s.settlement_date
               FROM am_position_ledger p LEFT JOIN am_position_event_settlement s
                 ON s.position_event_id=p.position_event_id
               WHERE p.account_id=? AND p.instrument_id=?
                 AND p.created_at_utc>=? AND p.created_at_utc<=?
               ORDER BY p.created_at_utc, p.position_event_id""",
            (account_id, instrument_id, opening[3], as_of.isoformat()),
        ):
            if settlement_record is None:
                raise ReconciliationError("position event settlement evidence is missing")
            value = Decimal(quantity)
            total += value
            if total < 0:
                raise ReconciliationError("position quantity becomes negative during replay")
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
            observed = broker_sellable_constraint.observed_at_utc.astimezone(timezone.utc)
            valid_until = broker_sellable_constraint.valid_until_utc.astimezone(timezone.utc)
            if not observed <= as_of <= valid_until:
                raise ReconciliationError("broker sellable constraint is future or stale")
            evidence = self._conn.execute(
                "SELECT 1 FROM am_raw_api_response WHERE raw_response_id=?",
                (broker_sellable_constraint.source_response_id,),
            ).fetchone()
            if evidence is None:
                raise ReconciliationError("broker constraint raw evidence is missing")
            available = min(available, broker_sellable_constraint.value)
        from asset_management.ledger.tax_lots import TaxLotLedger
        lots = TaxLotLedger(self._conn).lots(
            account_id=account_id, instrument_id=instrument_id, as_of_utc=as_of
        )
        remaining = [lot for lot in lots if lot.remaining_quantity > 0]
        if any(lot.currency != opening[0] for lot in lots):
            raise ReconciliationError("position tax-lot currency conflicts with native currency")
        lot_quantity = sum((lot.remaining_quantity for lot in remaining), Decimal(0))
        average_cost = (
            sum((lot.remaining_quantity * (lot.price + lot.commission / lot.quantity)
                 for lot in remaining), Decimal(0)) / lot_quantity
            if lot_quantity > 0 else Decimal(0)
        )
        return PositionState(account_id, instrument_id, opening[0], total,
                             average_cost, settled, total - settled,
                             reserved, available)
