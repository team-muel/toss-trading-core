from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3
from typing import Mapping

from asset_management.account.orders import assert_order_transition
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.enums import OrderState
from asset_management.domain.errors import ReconciliationError
from asset_management.ledger.cash import BrokerConstraint, CashLedger, CashState
from asset_management.ledger.positions import PositionLedger, PositionState


@dataclass(frozen=True, slots=True)
class LedgerReplayResult:
    cash: tuple[CashState, ...]
    positions: tuple[PositionState, ...]


class LedgerReplay:
    """Rebuilds state solely from immutable openings, events, and reservations."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._cash = CashLedger(conn)
        self._positions = PositionLedger(conn)

    def rebuild(self, *, account_id: str, as_of_utc: datetime,
                currencies: tuple[str, ...] | None = None,
                instruments: tuple[str, ...] | None = None,
                buying_power: Mapping[str, BrokerConstraint] | None = None,
                sellable: Mapping[str, BrokerConstraint] | None = None) -> LedgerReplayResult:
        self._validate_event_streams(account_id)
        currencies = currencies or tuple(row[0] for row in self._conn.execute(
            "SELECT currency FROM am_cash_opening_balance WHERE account_id=? ORDER BY currency",
            (account_id,),
        ))
        instruments = instruments or tuple(row[0] for row in self._conn.execute(
            "SELECT instrument_id FROM am_position_opening_balance WHERE account_id=? ORDER BY instrument_id",
            (account_id,),
        ))
        buying_power = buying_power or {}
        sellable = sellable or {}
        return LedgerReplayResult(
            tuple(self._cash.state(account_id=account_id, currency=currency,
                                   as_of_utc=as_of_utc,
                                   broker_buying_power_constraint=buying_power.get(currency))
                  for currency in currencies),
            tuple(self._positions.state(account_id=account_id, instrument_id=instrument,
                                        as_of_utc=as_of_utc,
                                        broker_sellable_constraint=sellable.get(instrument))
                  for instrument in instruments),
        )

    def _validate_event_streams(self, account_id: str) -> None:
        self._validate_raw_evidence(account_id)
        self._validate_order_states(account_id)
        self._validate_execution_chain(account_id)
        for table in ("am_cash_reservation_event", "am_position_reservation_event"):
            rows = self._conn.execute(
                f"""SELECT broker_order_id, sequence_no, observed_at_utc
                    FROM {table} WHERE account_id=?
                    ORDER BY broker_order_id, sequence_no""",
                (account_id,),
            )
            by_order: dict[str, list[tuple[int, datetime]]] = {}
            for order_id, sequence, observed in rows:
                by_order.setdefault(str(order_id), []).append(
                    (int(sequence), datetime.fromisoformat(str(observed)))
                )
            for values in by_order.values():
                if [item[0] for item in values] != list(range(1, len(values) + 1)):
                    raise ReconciliationError(f"{table} sequence has a gap or duplicate")
                if any(current[1] < previous[1] for previous, current in zip(values, values[1:])):
                    raise ReconciliationError(f"{table} observation time moves backwards")
        self._validate_reservations(account_id)
        self._validate_postings(account_id)
        missing_settlement = self._conn.execute(
            """SELECT COUNT(*) FROM am_position_ledger p
               LEFT JOIN am_position_event_settlement s USING(position_event_id)
               WHERE p.account_id=? AND s.position_event_id IS NULL""", (account_id,),
        ).fetchone()[0]
        if missing_settlement:
            raise ReconciliationError("position event settlement evidence is missing")

    def _validate_raw_evidence(self, account_id: str) -> None:
        raw_ids = self._conn.execute(
            """SELECT source_response_id FROM am_broker_order WHERE account_id=?
               UNION SELECT e.source_response_id FROM am_order_state_event e
                 JOIN am_broker_order o USING(broker_order_id) WHERE o.account_id=?
               UNION SELECT s.source_response_id FROM am_execution_snapshot s
                 JOIN am_broker_order o USING(broker_order_id) WHERE o.account_id=?
               UNION SELECT source_response_id FROM am_cash_reservation_event WHERE account_id=?
               UNION SELECT source_response_id FROM am_position_reservation_event WHERE account_id=?""",
            (account_id, account_id, account_id, account_id, account_id),
        )
        store = SQLiteRawResponseStore(self._conn)
        for (raw_id,) in raw_ids:
            if raw_id is None:
                continue
            try:
                store.verified(str(raw_id))
            except (KeyError, ValueError) as error:
                raise ReconciliationError(f"raw evidence verification failed: {raw_id}") from error

    def _validate_order_states(self, account_id: str) -> None:
        rows = self._conn.execute(
            """SELECT e.broker_order_id, e.sequence_no, e.previous_state, e.state,
                      e.observed_at_utc
               FROM am_order_state_event e JOIN am_broker_order o USING(broker_order_id)
               WHERE o.account_id=? ORDER BY e.broker_order_id, e.sequence_no""",
            (account_id,),
        )
        previous: dict[str, tuple[int, OrderState, datetime]] = {}
        for order_id_raw, sequence_raw, recorded_previous, state_raw, observed_raw in rows:
            order_id, sequence = str(order_id_raw), int(sequence_raw)
            state, observed = OrderState(str(state_raw)), datetime.fromisoformat(str(observed_raw))
            prior = previous.get(order_id)
            expected_sequence = 1 if prior is None else prior[0] + 1
            expected_previous = None if prior is None else prior[1]
            if sequence != expected_sequence or recorded_previous != (
                expected_previous.value if expected_previous else None
            ):
                raise ReconciliationError("order state chain is not contiguous")
            if prior is not None and observed < prior[2]:
                raise ReconciliationError("order state observation time moves backwards")
            try:
                assert_order_transition(expected_previous, state)
            except Exception as error:
                raise ReconciliationError("order state transition is invalid during replay") from error
            previous[order_id] = (sequence, state, observed)

    def _validate_execution_chain(self, account_id: str) -> None:
        rows = self._conn.execute(
            """SELECT s.execution_snapshot_id, s.broker_order_id, s.sequence_no,
                      s.cumulative_quantity_decimal, s.cumulative_amount_decimal,
                      s.average_price_decimal, s.cumulative_commission_decimal,
                      s.cumulative_tax_decimal, s.observed_at_utc, s.content_hash,
                      d.from_snapshot_id, d.to_snapshot_id, d.quantity_decimal,
                      d.amount_decimal, d.commission_decimal, d.tax_decimal
               FROM am_execution_snapshot s JOIN am_broker_order o USING(broker_order_id)
               LEFT JOIN am_execution_delta d ON d.to_snapshot_id=s.execution_snapshot_id
               WHERE o.account_id=? ORDER BY s.broker_order_id, s.sequence_no""",
            (account_id,),
        )
        previous: dict[str, tuple[str, int, tuple[Decimal, ...], datetime]] = {}
        for row in rows:
            snapshot_id, order_id, sequence = str(row[0]), str(row[1]), int(row[2])
            values = tuple(Decimal(str(value)) for value in (row[3], row[4], row[6], row[7]))
            average = Decimal(str(row[5])) if row[5] is not None else None
            observed = datetime.fromisoformat(str(row[8]))
            payload = {
                "quantity": format(values[0], "f"), "amount": format(values[1], "f"),
                "average": None if average is None else format(average, "f"),
                "commission": format(values[2], "f"), "tax": format(values[3], "f"),
            }
            expected_hash = sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            prior = previous.get(order_id)
            expected_sequence = 1 if prior is None else prior[1] + 1
            prior_values = (Decimal(0),) * 4 if prior is None else prior[2]
            if sequence != expected_sequence or observed < (prior[3] if prior else observed):
                raise ReconciliationError("execution snapshot chain is not chronological")
            if any(current < old for current, old in zip(values, prior_values)):
                raise ReconciliationError("cumulative execution values decrease during replay")
            if str(row[9]) != expected_hash:
                raise ReconciliationError("execution snapshot content hash mismatch")
            if row[11] != snapshot_id or row[10] != (prior[0] if prior else None):
                raise ReconciliationError("execution delta snapshot linkage is invalid")
            expected_delta = tuple(current - old for current, old in zip(values, prior_values))
            actual_delta = tuple(Decimal(str(value)) for value in row[12:16])
            if actual_delta != expected_delta:
                raise ReconciliationError("execution delta does not match cumulative snapshots")
            previous[order_id] = (snapshot_id, sequence, values, observed)

    def _validate_reservations(self, account_id: str) -> None:
        for table, identity_column, amount_column in (
            ("am_cash_reservation_event", "currency", "reserved_amount_decimal"),
            ("am_position_reservation_event", "instrument_id", "reserved_quantity_decimal"),
        ):
            rows = self._conn.execute(
                f"""SELECT r.broker_order_id, r.sequence_no, r.account_id,
                           r.{identity_column}, r.{amount_column}, r.status, o.payload_json
                    FROM {table} r JOIN am_broker_order o USING(broker_order_id)
                    WHERE o.account_id=? ORDER BY r.broker_order_id, r.sequence_no""",
                (account_id,),
            )
            identities: dict[str, tuple[str, str]] = {}
            latest_status: dict[str, str] = {}
            for order_id_raw, _, row_account, row_identity, value_raw, status, payload_raw in rows:
                order_id = str(order_id_raw)
                payload = json.loads(str(payload_raw))
                expected_identity = (
                    str(payload.get("currency", "")).upper()
                    if identity_column == "currency"
                    else str(payload.get("instrumentId", payload.get("symbol", "")))
                )
                current_identity = (str(row_account), str(row_identity))
                if current_identity != (account_id, expected_identity):
                    raise ReconciliationError(f"{table} identity conflicts with broker order")
                if order_id in identities and identities[order_id] != current_identity:
                    raise ReconciliationError(f"{table} identity changes within an order")
                identities[order_id] = current_identity
                value = Decimal(str(value_raw))
                if value < 0 or (str(status) == "RELEASED" and value != 0):
                    raise ReconciliationError(f"{table} amount or release state is invalid")
                latest_status[order_id] = str(status)
            for order_id, status in latest_status.items():
                terminal = self._conn.execute(
                    """SELECT state FROM am_order_state_event WHERE broker_order_id=?
                       ORDER BY sequence_no DESC LIMIT 1""", (order_id,),
                ).fetchone()
                if terminal is not None and str(terminal[0]) in {
                    "FILLED", "CANCELED", "REPLACED", "REJECTED"
                } and status == "RESERVED":
                    raise ReconciliationError("terminal order retains an open reservation")

    def _validate_postings(self, account_id: str) -> None:
        rows = self._conn.execute(
            """SELECT d.execution_delta_id, d.quantity_decimal, d.amount_decimal,
                      d.commission_decimal, d.tax_decimal, p.cash_event_id,
                      p.position_event_id, c.account_id, c.instrument_id, c.side,
                      c.currency, c.settlement_date, o.payload_json
               FROM am_execution_delta d JOIN am_broker_order o USING(broker_order_id)
               LEFT JOIN am_execution_posting p USING(execution_delta_id)
               LEFT JOIN am_execution_posting_context c USING(execution_delta_id)
               WHERE o.account_id=?""",
            (account_id,),
        )
        for row in rows:
            delta_id = str(row[0])
            quantity, amount, commission, tax = (Decimal(str(value)) for value in row[1:5])
            effectful = any(value != 0 for value in (quantity, amount, commission, tax))
            if not effectful and row[5] is None and row[6] is None and row[7] is None:
                continue
            if row[7] is None:
                raise ReconciliationError("execution delta is missing ledger posting or context")
            payload = json.loads(str(row[12]))
            identity = (
                str(row[7]), str(row[8]), str(row[9]), str(row[10])
            )
            expected_identity = (
                account_id, str(payload.get("instrumentId", payload.get("symbol", ""))),
                str(payload.get("side", "")).upper(), str(payload.get("currency", "")).upper(),
            )
            if identity != expected_identity or identity[2] not in {"BUY", "SELL"}:
                raise ReconciliationError("execution posting identity conflicts with broker order")
            context_details = self._conn.execute(
                """SELECT c.broker_order_id, d.broker_order_id, c.tax_policy_version,
                          c.fx_rate_decimal, c.context_hash, p.posted_at_utc, d.created_at_utc
                   FROM am_execution_posting_context c
                   JOIN am_execution_posting p USING(execution_delta_id)
                   JOIN am_execution_delta d USING(execution_delta_id)
                   WHERE c.execution_delta_id=?""",
                (delta_id,),
            ).fetchone()
            if context_details is None:
                raise ReconciliationError("execution delta is missing ledger posting or context")
            if str(context_details[0]) != str(context_details[1]):
                raise ReconciliationError("execution posting references the wrong broker order")
            canonical_context = {
                "account_id": identity[0], "instrument_id": identity[1],
                "side": identity[2], "currency": identity[3],
                "settlement_date": str(row[11]),
                "tax_policy_version": str(context_details[2]),
                "fx_rate": format(Decimal(str(context_details[3])), "f"),
            }
            expected_context_hash = sha256(
                json.dumps(canonical_context, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if str(context_details[4]) != expected_context_hash:
                raise ReconciliationError("execution posting context hash mismatch")
            if datetime.fromisoformat(str(context_details[5])) < datetime.fromisoformat(
                str(context_details[6])
            ):
                raise ReconciliationError("ledger posting predates execution delta")
            sign = Decimal("1") if identity[2] == "BUY" else Decimal("-1")
            expected_position = sign * quantity
            if expected_position != 0:
                position = self._conn.execute(
                    """SELECT account_id, instrument_id, quantity_delta_decimal
                       FROM am_position_ledger WHERE position_event_id=?""",
                    (row[6],),
                ).fetchone()
                settlement = self._conn.execute(
                    "SELECT settlement_date FROM am_position_event_settlement WHERE position_event_id=?",
                    (row[6],),
                ).fetchone()
                if position is None or settlement is None or (
                    str(position[0]), str(position[1]), Decimal(str(position[2])), str(settlement[0])
                ) != (identity[0], identity[1], expected_position, str(row[11])):
                    raise ReconciliationError("position posting does not match execution delta")
            elif row[6] is not None:
                raise ReconciliationError("zero-quantity execution has a position posting")
            expected_components = {}
            if amount != 0:
                expected_components["PRINCIPAL"] = (-amount if identity[2] == "BUY" else amount)
            if commission != 0:
                expected_components["COMMISSION"] = -commission
            if tax != 0:
                expected_components["TAX"] = -tax
            actual_components = {}
            for component, cash_event_id in self._conn.execute(
                """SELECT component, cash_event_id FROM am_execution_cash_component
                   WHERE execution_delta_id=?""", (delta_id,),
            ):
                cash = self._conn.execute(
                    """SELECT account_id, currency, amount_decimal, settlement_date, event_type
                       FROM am_cash_ledger WHERE cash_event_id=?""", (cash_event_id,),
                ).fetchone()
                if cash is None or (str(cash[0]), str(cash[1]), str(cash[3])) != (
                    identity[0], identity[3], str(row[11])
                ):
                    raise ReconciliationError("cash posting identity or settlement is invalid")
                expected_type = {
                    "PRINCIPAL": "TRADE_COST" if identity[2] == "BUY" else "TRADE_PROCEEDS",
                    "COMMISSION": "COMMISSION", "TAX": "TAX",
                }.get(str(component))
                if str(cash[4]) != expected_type:
                    raise ReconciliationError("cash posting event type is invalid")
                actual_components[str(component)] = Decimal(str(cash[2]))
            if actual_components != expected_components:
                raise ReconciliationError("cash components do not match execution delta")
            principal_id = next((cash_id for component, cash_id in self._conn.execute(
                "SELECT component, cash_event_id FROM am_execution_cash_component WHERE execution_delta_id=?",
                (delta_id,),
            ) if component == "PRINCIPAL"), None)
            if row[5] != principal_id:
                raise ReconciliationError("execution posting principal link is invalid")
