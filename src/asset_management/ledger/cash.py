from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import sqlite3
from uuid import uuid4

from asset_management.domain.errors import DataQualityError, ReconciliationError


class CashEventType(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRADE_COST = "TRADE_COST"
    TRADE_PROCEEDS = "TRADE_PROCEEDS"
    COMMISSION = "COMMISSION"
    TAX = "TAX"
    DIVIDEND = "DIVIDEND"
    WITHHOLDING = "WITHHOLDING"
    INTEREST = "INTEREST"
    FX_CONVERSION_IN = "FX_CONVERSION_IN"
    FX_CONVERSION_OUT = "FX_CONVERSION_OUT"
    CORPORATE_ACTION_CASH = "CORPORATE_ACTION_CASH"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"


def exact(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ReconciliationError(f"invalid {name}") from None
    if not result.is_finite():
        raise ReconciliationError(f"{name} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class CashState:
    account_id: str
    currency: str
    as_of_utc: datetime
    settled_cash: Decimal
    unsettled_cash: Decimal
    reserved_cash: Decimal
    available_cash: Decimal
    broker_buying_power_constraint: Decimal | None
    orderable_cash: Decimal


@dataclass(frozen=True, slots=True)
class BrokerConstraint:
    value: Decimal
    observed_at_utc: datetime
    valid_until_utc: datetime
    source_response_id: str

    def __post_init__(self) -> None:
        if (self.observed_at_utc.tzinfo is None or self.valid_until_utc.tzinfo is None
                or not self.source_response_id):
            raise ReconciliationError("broker constraint requires time and raw evidence")
        if self.valid_until_utc < self.observed_at_utc:
            raise ReconciliationError("broker constraint validity interval is invalid")
        value = exact(self.value, "broker constraint")
        if value < 0:
            raise ReconciliationError("broker constraint cannot be negative")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class OpenBuyOrder:
    broker_order_id: str
    account_id: str
    currency: str
    remaining_quantity: Decimal | None = None
    limit_price: Decimal | None = None
    remaining_amount: Decimal | None = None
    expected_fees: Decimal = Decimal("0")

    def reserve(self) -> Decimal:
        fees = exact(self.expected_fees, "expected fees")
        if fees < 0:
            raise ReconciliationError("expected fees cannot be negative")
        if self.remaining_amount is not None:
            principal = exact(self.remaining_amount, "remaining amount")
        elif self.remaining_quantity is not None and self.limit_price is not None:
            principal = exact(self.remaining_quantity, "remaining quantity") * exact(
                self.limit_price, "limit price"
            )
        else:
            raise DataQualityError("OPEN_ORDER_PRICE_OR_AMOUNT_UNKNOWN")
        if principal < 0:
            raise ReconciliationError("open-order reserve cannot be negative")
        return principal + fees


class CashLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_opening(self, *, account_id: str, currency: str, as_of_utc: datetime,
                       opening_balance: object, evidence: str | None,
                       approved_by: str | None) -> str:
        if not evidence or not approved_by:
            raise DataQualityError("OPENING_BALANCE_UNKNOWN")
        evidence_exists = self._conn.execute(
            """SELECT 1 FROM am_raw_api_response WHERE raw_response_id=?
               UNION ALL SELECT 1 FROM am_dataset_manifest WHERE dataset_manifest_id=? LIMIT 1""",
            (evidence, evidence),
        ).fetchone()
        if evidence_exists is None:
            raise DataQualityError("OPENING_BALANCE_EVIDENCE_MISSING")
        if as_of_utc.tzinfo is None:
            raise ReconciliationError("opening balance timestamp must be timezone-aware")
        balance = exact(opening_balance, "opening balance")
        if balance < 0:
            raise ReconciliationError("opening cash cannot be negative in long-only mode")
        opening_id = str(uuid4())
        self._conn.execute(
            "INSERT INTO am_cash_opening_balance VALUES (?, ?, ?, ?, ?, ?, ?)",
            (opening_id, account_id, currency.upper(),
             as_of_utc.astimezone(timezone.utc).isoformat(),
             format(balance, "f"), evidence, approved_by),
        )
        return opening_id

    def append_event(self, *, account_id: str, currency: str, amount: object,
                     event_type: CashEventType, created_at_utc: datetime,
                     idempotency_key: str, settlement_date: date | None = None,
                     reason: str | None = None, approved_by: str | None = None) -> str:
        if created_at_utc.tzinfo is None:
            raise ReconciliationError("cash event timestamp must be timezone-aware")
        if event_type is CashEventType.MANUAL_ADJUSTMENT and (not reason or not approved_by):
            raise ReconciliationError("MANUAL_ADJUSTMENT requires reason and approved_by")
        event_amount = exact(amount, "cash amount")
        positive_types = {
            CashEventType.DEPOSIT, CashEventType.TRADE_PROCEEDS, CashEventType.DIVIDEND,
            CashEventType.INTEREST, CashEventType.FX_CONVERSION_IN,
        }
        negative_types = {
            CashEventType.WITHDRAWAL, CashEventType.TRADE_COST, CashEventType.COMMISSION,
            CashEventType.TAX, CashEventType.WITHHOLDING, CashEventType.FX_CONVERSION_OUT,
        }
        if event_type in positive_types and event_amount < 0:
            raise ReconciliationError(f"{event_type} must not be negative")
        if event_type in negative_types and event_amount > 0:
            raise ReconciliationError(f"{event_type} must not be positive")
        existing = self._conn.execute(
            """SELECT m.cash_event_id, l.account_id, l.currency, l.amount_decimal,
                      l.settlement_date, l.event_type, l.created_at_utc, m.reason, m.approved_by
               FROM am_cash_event_metadata m JOIN am_cash_ledger l USING(cash_event_id)
               WHERE m.idempotency_key=?""",
            (idempotency_key,),
        ).fetchone()
        if existing:
            requested = (
                account_id, currency.upper(), event_amount,
                settlement_date.isoformat() if settlement_date else None,
                str(event_type), created_at_utc.astimezone(timezone.utc), reason, approved_by,
            )
            persisted = (
                str(existing[1]), str(existing[2]), Decimal(existing[3]), existing[4],
                str(existing[5]), datetime.fromisoformat(str(existing[6])), existing[7], existing[8],
            )
            if requested != persisted:
                raise ReconciliationError("idempotency key conflicts with existing cash event")
            return str(existing[0])
        event_id = str(uuid4())
        instant = created_at_utc.astimezone(timezone.utc).isoformat()
        self._conn.execute("SAVEPOINT am_cash_event")
        try:
            if event_type is CashEventType.MANUAL_ADJUSTMENT:
                self._conn.execute(
                    "INSERT INTO am_manual_cash_authorization VALUES (?, ?, ?, ?)",
                    (event_id, reason, approved_by, instant),
                )
            self._conn.execute(
                "INSERT INTO am_cash_ledger VALUES (?, NULL, ?, ?, ?, ?, ?, ?)",
                (event_id, account_id, currency.upper(), format(event_amount, "f"),
                 settlement_date.isoformat() if settlement_date else None, event_type, instant),
            )
            self._conn.execute(
                "INSERT INTO am_cash_event_metadata VALUES (?, ?, ?, ?)",
                (event_id, idempotency_key, reason, approved_by),
            )
            self._conn.execute("RELEASE SAVEPOINT am_cash_event")
        except Exception:
            self._conn.execute("ROLLBACK TO SAVEPOINT am_cash_event")
            self._conn.execute("RELEASE SAVEPOINT am_cash_event")
            raise
        return event_id

    def reserve_open_order(self, order: OpenBuyOrder, *, source_response_id: str,
                           observed_at_utc: datetime, released: bool = False) -> str:
        if observed_at_utc.tzinfo is None:
            raise ReconciliationError("reservation timestamp must be timezone-aware")
        instant = observed_at_utc.astimezone(timezone.utc)
        amount = Decimal(0) if released else order.reserve()
        status = "RELEASED" if released else "RESERVED"
        existing = self._conn.execute(
            """SELECT reservation_event_id, account_id, currency,
                      reserved_amount_decimal, status, observed_at_utc
               FROM am_cash_reservation_event
               WHERE broker_order_id=? AND source_response_id=?""",
            (order.broker_order_id, source_response_id),
        ).fetchone()
        if existing:
            persisted = (
                str(existing[1]), str(existing[2]), Decimal(existing[3]),
                str(existing[4]), datetime.fromisoformat(str(existing[5])),
            )
            requested = (order.account_id, order.currency.upper(), amount, status, instant)
            if requested != persisted:
                raise ReconciliationError("reservation evidence conflicts with existing cash reservation")
            return str(existing[0])
        latest = self._conn.execute(
            """SELECT sequence_no, account_id, currency, observed_at_utc
               FROM am_cash_reservation_event WHERE broker_order_id=?
               ORDER BY sequence_no DESC LIMIT 1""",
            (order.broker_order_id,),
        ).fetchone()
        if latest is not None:
            if (str(latest[1]), str(latest[2])) != (order.account_id, order.currency.upper()):
                raise ReconciliationError("cash reservation identity cannot change")
            if instant < datetime.fromisoformat(str(latest[3])):
                raise ReconciliationError("cash reservation time cannot move backwards")
        sequence = int(latest[0]) + 1 if latest is not None else 1
        event_id = str(uuid4())
        self._conn.execute(
            "INSERT INTO am_cash_reservation_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, order.broker_order_id, sequence, order.account_id,
             order.currency.upper(), format(amount, "f"),
             status, source_response_id, instant.isoformat()),
        )
        return event_id

    def state(self, *, account_id: str, currency: str, as_of_utc: datetime,
              broker_buying_power_constraint: BrokerConstraint | None = None) -> CashState:
        if as_of_utc.tzinfo is None:
            raise ReconciliationError("cash state timestamp must be timezone-aware")
        currency = currency.upper()
        as_of = as_of_utc.astimezone(timezone.utc)
        opening = self._conn.execute(
            "SELECT opening_balance_decimal, as_of_utc FROM am_cash_opening_balance WHERE account_id=? AND currency=?",
            (account_id, currency),
        ).fetchone()
        if opening is None or datetime.fromisoformat(opening[1]) > as_of:
            raise DataQualityError("OPENING_BALANCE_UNKNOWN")
        settled, unsettled = Decimal(opening[0]), Decimal(0)
        for amount, settlement, event_id, event_type in self._conn.execute(
            """SELECT amount_decimal, settlement_date, cash_event_id, event_type
               FROM am_cash_ledger
               WHERE account_id=? AND currency=? AND created_at_utc>=? AND created_at_utc<=?
               ORDER BY created_at_utc, cash_event_id""",
            (account_id, currency, opening[1], as_of.isoformat()),
        ):
            if event_type == CashEventType.MANUAL_ADJUSTMENT:
                authorization = self._conn.execute(
                    """SELECT reason, approved_by FROM am_manual_cash_authorization
                       WHERE cash_event_id=?""", (event_id,),
                ).fetchone()
                metadata = self._conn.execute(
                    """SELECT reason, approved_by FROM am_cash_event_metadata
                       WHERE cash_event_id=?""", (event_id,),
                ).fetchone()
                if authorization is None or metadata is None or tuple(authorization) != tuple(metadata):
                    raise ReconciliationError("manual cash authorization is missing or conflicting")
            value = Decimal(amount)
            if settlement is not None and date.fromisoformat(settlement) > as_of.date():
                unsettled += value
            else:
                settled += value
                if settled < 0:
                    raise ReconciliationError("settled cash becomes negative during replay")
        reserved_rows = self._conn.execute(
            """WITH latest AS (SELECT broker_order_id, MAX(sequence_no) seq
                 FROM am_cash_reservation_event WHERE account_id=? AND currency=?
                 AND observed_at_utc>=? AND observed_at_utc<=? GROUP BY broker_order_id)
               SELECT e.reserved_amount_decimal
               FROM latest l JOIN am_cash_reservation_event e
               ON e.broker_order_id=l.broker_order_id AND e.sequence_no=l.seq
               WHERE e.status='RESERVED'""",
            (account_id, currency, opening[1], as_of.isoformat()),
        )
        reserved = sum((Decimal(row[0]) for row in reserved_rows), Decimal(0))
        available = settled - reserved
        if available < 0:
            raise ReconciliationError("reserved cash exceeds settled cash")
        constraint = None
        if broker_buying_power_constraint is not None:
            observed = broker_buying_power_constraint.observed_at_utc.astimezone(timezone.utc)
            valid_until = broker_buying_power_constraint.valid_until_utc.astimezone(timezone.utc)
            if not observed <= as_of <= valid_until:
                raise ReconciliationError("broker constraint is future or stale")
            evidence = self._conn.execute(
                "SELECT 1 FROM am_raw_api_response WHERE raw_response_id=?",
                (broker_buying_power_constraint.source_response_id,),
            ).fetchone()
            if evidence is None:
                raise ReconciliationError("broker constraint raw evidence is missing")
            constraint = broker_buying_power_constraint.value
        orderable = min(available, constraint) if constraint is not None else available
        return CashState(account_id, currency, as_of, settled, unsettled, reserved,
                         available, constraint, orderable)
