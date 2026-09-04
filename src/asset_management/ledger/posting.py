from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from asset_management.account.executions import ExecutionDelta
from asset_management.domain.errors import ReconciliationError
from asset_management.ledger.cash import exact


@dataclass(frozen=True, slots=True)
class ExecutionPostingContext:
    account_id: str
    instrument_id: str
    side: str
    currency: str
    settlement_date: date
    tax_policy_version: str
    fx_rate: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        side = self.side.strip().upper()
        if side not in {"BUY", "SELL"}:
            raise ReconciliationError(f"unsupported execution side: {self.side!r}")
        if not self.account_id or not self.instrument_id or not self.currency:
            raise ReconciliationError("execution posting context is incomplete")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "currency", self.currency.strip().upper())
        if not isinstance(self.settlement_date, date):
            raise ReconciliationError("execution settlement date is required")
        fx_rate = exact(self.fx_rate, "tax-lot FX rate")
        object.__setattr__(self, "fx_rate", fx_rate)
        if fx_rate <= 0 or not self.tax_policy_version:
            raise ReconciliationError("tax-lot FX rate and policy version are required")
        if self.tax_policy_version != "FIFO-v1":
            raise ReconciliationError("unsupported tax policy version")

    def canonical_payload(self) -> dict[str, str]:
        return {
            "account_id": self.account_id, "instrument_id": self.instrument_id,
            "side": self.side, "currency": self.currency,
            "settlement_date": self.settlement_date.isoformat(),
            "tax_policy_version": self.tax_policy_version,
            "fx_rate": format(self.fx_rate, "f"),
        }


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
        stored = self._conn.execute(
            """SELECT d.broker_order_id, d.quantity_decimal, d.amount_decimal,
                      d.commission_decimal, d.tax_decimal, o.account_id, o.payload_json
               FROM am_execution_delta d JOIN am_broker_order o
                 ON o.broker_order_id=d.broker_order_id
               WHERE d.execution_delta_id=?""",
            (delta.execution_delta_id,),
        ).fetchone()
        if stored is None:
            raise ReconciliationError("execution delta is not persisted")
        if (delta.broker_order_id, delta.quantity, delta.amount, delta.commission, delta.tax) != (
            str(stored[0]), Decimal(stored[1]), Decimal(stored[2]), Decimal(stored[3]), Decimal(stored[4])
        ):
            raise ReconciliationError("caller execution delta conflicts with persisted delta")
        order_payload = json.loads(stored[6])
        expected_instrument = order_payload.get("instrumentId", order_payload.get("symbol"))
        expected_side = str(order_payload.get("side", "")).upper()
        expected_currency = str(order_payload.get("currency", "")).upper()
        if not expected_instrument or not expected_side or not expected_currency:
            raise ReconciliationError("broker order identity is incomplete")
        if (context.account_id, context.instrument_id, context.side, context.currency) != (
            str(stored[5]), str(expected_instrument), expected_side, expected_currency
        ):
            raise ReconciliationError("posting context conflicts with broker order identity")
        opening = self._conn.execute(
            """SELECT native_currency FROM am_position_opening_balance
               WHERE account_id=? AND instrument_id=?""",
            (context.account_id, context.instrument_id),
        ).fetchone()
        if opening is not None and str(opening[0]) != context.currency:
            raise ReconciliationError(
                "execution currency conflicts with position native currency"
            )
        context_payload = context.canonical_payload()
        context_hash = sha256(
            json.dumps(context_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        existing = self._conn.execute(
            """SELECT p.cash_event_id, p.position_event_id, c.context_hash
               FROM am_execution_posting p LEFT JOIN am_execution_posting_context c
                 ON c.execution_delta_id=p.execution_delta_id
               WHERE p.execution_delta_id=?""",
            (delta.execution_delta_id,),
        ).fetchone()
        direction = Decimal("1") if context.side == "BUY" else Decimal("-1")
        quantity_delta = direction * delta.quantity
        principal_direction = Decimal("-1") if context.side == "BUY" else Decimal("1")
        cash_delta = principal_direction * delta.amount - delta.commission - delta.tax
        if existing:
            if existing[2] != context_hash:
                raise ReconciliationError("replayed posting context conflicts with original posting")
            return ExecutionPosting(
                delta.execution_delta_id, existing[0], existing[1], cash_delta, quantity_delta
            )

        cash_event_id = (
            str(uuid5(NAMESPACE_URL, f"cash:{delta.execution_delta_id}"))
            if delta.amount != 0 else None
        )
        position_event_id = (
            str(uuid5(NAMESPACE_URL, f"position:{delta.execution_delta_id}"))
            if quantity_delta != 0 else None
        )
        instant = posted_at_utc.astimezone(timezone.utc).isoformat()
        self._conn.execute("SAVEPOINT am_execution_posting")
        try:
            self._conn.execute(
                """INSERT INTO am_execution_posting_context VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (delta.execution_delta_id, delta.broker_order_id, context.account_id,
                 context.instrument_id, context.side, context.currency,
                 context.settlement_date.isoformat(), context.tax_policy_version,
                 format(context.fx_rate, "f"), context_hash),
            )
            if cash_event_id:
                self._conn.execute(
                    """INSERT INTO am_cash_ledger
                       (cash_event_id, execution_id, account_id, currency,
                        amount_decimal, settlement_date, event_type, created_at_utc)
                       VALUES (?, NULL, ?, ?, ?, ?, ?, ?)""",
                    (cash_event_id, context.account_id, context.currency,
                     format(principal_direction * delta.amount, "f"),
                     context.settlement_date.isoformat(),
                     "TRADE_COST" if context.side == "BUY" else "TRADE_PROCEEDS", instant),
                )
                self._conn.execute(
                    "INSERT INTO am_execution_cash_component VALUES (?, 'PRINCIPAL', ?)",
                    (delta.execution_delta_id, cash_event_id),
                )
            for component, value in (("COMMISSION", delta.commission), ("TAX", delta.tax)):
                if value == 0:
                    continue
                component_id = str(uuid5(NAMESPACE_URL, f"cash:{component.lower()}:{delta.execution_delta_id}"))
                self._conn.execute(
                    """INSERT INTO am_cash_ledger
                       (cash_event_id, execution_id, account_id, currency,
                        amount_decimal, settlement_date, event_type, created_at_utc)
                       VALUES (?, NULL, ?, ?, ?, ?, ?, ?)""",
                    (component_id, context.account_id, context.currency, format(-value, "f"),
                     context.settlement_date.isoformat(),
                     component, instant),
                )
                self._conn.execute(
                    "INSERT INTO am_execution_cash_component VALUES (?, ?, ?)",
                    (delta.execution_delta_id, component, component_id),
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
                    "INSERT INTO am_position_event_settlement VALUES (?, ?)",
                    (position_event_id, context.settlement_date.isoformat()),
                )
                from asset_management.ledger.tax_lots import TaxLotLedger
                lots = TaxLotLedger(self._conn)
                trade_date = posted_at_utc.astimezone(timezone.utc).date()
                if context.side == "BUY":
                    price = delta.amount / delta.quantity
                    lots.acquire(
                        execution_delta_id=delta.execution_delta_id,
                        account_id=context.account_id, instrument_id=context.instrument_id,
                        acquisition_date=trade_date, settlement_date=context.settlement_date,
                        quantity=delta.quantity, price=price, commission=delta.commission,
                        currency=context.currency, fx_rate=context.fx_rate,
                        tax_policy_version=context.tax_policy_version,
                        observed_at_utc=posted_at_utc,
                    )
                else:
                    lots.dispose_fifo(
                        execution_delta_id=delta.execution_delta_id,
                        account_id=context.account_id, instrument_id=context.instrument_id,
                        quantity=delta.quantity, disposal_date=trade_date,
                        observed_at_utc=posted_at_utc,
                        tax_policy_version=context.tax_policy_version,
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
