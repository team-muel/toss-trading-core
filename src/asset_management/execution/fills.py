"""Fail-closed order observation and cumulative-fill processing."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Callable, Mapping
from uuid import uuid4

from asset_management.account.executions import (
    ExecutionDelta,
    ExecutionSnapshot,
    ExecutionSnapshotRepository,
)
from asset_management.account.orders import (
    OrderStateEvent,
    OrderStateRepository,
    normalize_broker_order_state,
    TERMINAL_ORDER_STATES,
)
from asset_management.domain.enums import OrderState
from asset_management.domain.errors import ExecutionError
from asset_management.ledger.posting import (
    ExecutionLedgerPoster,
    ExecutionPosting,
    ExecutionPostingContext,
)


@dataclass(frozen=True, slots=True)
class ObservedOrder:
    state_event: OrderStateEvent
    execution_snapshot: ExecutionSnapshot | None
    execution_delta: ExecutionDelta | None
    posting: ExecutionPosting | None


class OrderObservationService:
    def __init__(
        self, states: OrderStateRepository, executions: ExecutionSnapshotRepository
    ) -> None:
        if states._conn is not executions._conn:
            raise ValueError("order state and execution repositories must share one transaction")
        self._states = states
        self._executions = executions
        self._conn = states._conn
        self._poster = ExecutionLedgerPoster(self._conn)

    def observe(
        self, *, broker_order_id: str, raw_state: object, observed_at_utc: datetime,
        source_response_id: str, execution: Mapping[str, object] | None = None,
        posting_context: ExecutionPostingContext | None = None,
    ) -> ObservedOrder:
        normalized_state = normalize_broker_order_state(raw_state)
        # UNKNOWN is itself material evidence. Preserve it before raising fail-closed.
        if normalized_state is OrderState.UNKNOWN:
            self._states.append_broker_state(
                broker_order_id=broker_order_id, raw_state=raw_state,
                observed_at_utc=observed_at_utc, source_response_id=source_response_id,
            )
        self._conn.execute("SAVEPOINT am_order_observation")
        try:
            if normalized_state in {OrderState.PARTIALLY_FILLED, OrderState.FILLED}:
                if execution is None or posting_context is None:
                    raise ExecutionError("fill-bearing state requires execution and posting context")
            state = self._states.append_broker_state(
                broker_order_id=broker_order_id, raw_state=raw_state,
                observed_at_utc=observed_at_utc, source_response_id=source_response_id,
            )
            if execution is None:
                result = ObservedOrder(state, None, None, None)
            else:
                snapshot, delta = self._executions.append(
                    broker_order_id=broker_order_id,
                    cumulative_quantity=execution.get("filledQuantity", "0"),
                    cumulative_amount=execution.get("filledAmount", "0"),
                    average_price=execution.get("averageFilledPrice"),
                    cumulative_commission=execution.get("commission", "0"),
                    cumulative_tax=execution.get("tax", "0"),
                    observed_at_utc=observed_at_utc,
                    source_response_id=source_response_id,
                )
                if state.state is OrderState.FILLED and snapshot.cumulative_quantity <= 0:
                    raise ExecutionError("FILLED order requires positive cumulative fill")
                if state.state is OrderState.PARTIALLY_FILLED and snapshot.cumulative_quantity <= 0:
                    raise ExecutionError("PARTIALLY_FILLED requires positive cumulative fill")
                order_payload = json.loads(self._conn.execute(
                    "SELECT payload_json FROM am_broker_order WHERE broker_order_id=?",
                    (broker_order_id,),
                ).fetchone()[0])
                ordered_raw = order_payload.get("quantity", order_payload.get("orderQuantity"))
                if ordered_raw is not None:
                    try:
                        ordered = Decimal(str(ordered_raw))
                    except (InvalidOperation, ValueError):
                        raise ExecutionError("broker order quantity is invalid") from None
                    if not ordered.is_finite() or ordered <= 0:
                        raise ExecutionError("broker order quantity must be positive")
                    if state.state is OrderState.FILLED and snapshot.cumulative_quantity != ordered:
                        raise ExecutionError("FILLED cumulative quantity must equal ordered quantity")
                    if (state.state is OrderState.PARTIALLY_FILLED
                            and snapshot.cumulative_quantity >= ordered):
                        raise ExecutionError(
                            "PARTIALLY_FILLED cumulative quantity must be below ordered quantity"
                        )
                posting_delta = delta or self._executions.delta_for_snapshot(
                    snapshot.execution_snapshot_id
                )
                if posting_delta is not None and posting_delta.has_ledger_effect and posting_context is None:
                    raise ExecutionError("execution delta requires ledger posting context")
                posting = self._poster.post(
                    posting_delta, posting_context, posted_at_utc=observed_at_utc
                ) if posting_delta is not None and posting_context is not None else None
                result = ObservedOrder(state, snapshot, delta, posting)
            if state.state in TERMINAL_ORDER_STATES:
                self._release_reservations(
                    broker_order_id, source_response_id, observed_at_utc
                )
            self._conn.execute("RELEASE SAVEPOINT am_order_observation")
            return result
        except Exception:
            self._conn.execute("ROLLBACK TO SAVEPOINT am_order_observation")
            self._conn.execute("RELEASE SAVEPOINT am_order_observation")
            raise

    def _release_reservations(
        self, broker_order_id: str, source_response_id: str, observed_at_utc: datetime
    ) -> None:
        instant = observed_at_utc.astimezone(timezone.utc).isoformat()
        cash = self._conn.execute(
            """SELECT account_id, currency, MAX(sequence_no) FROM am_cash_reservation_event
               WHERE broker_order_id=? GROUP BY account_id, currency""",
            (broker_order_id,),
        ).fetchall()
        for account_id, currency, sequence in cash:
            latest = self._conn.execute(
                "SELECT status FROM am_cash_reservation_event WHERE broker_order_id=? AND sequence_no=?",
                (broker_order_id, sequence),
            ).fetchone()
            if latest and latest[0] == "RESERVED":
                conflict = self._conn.execute(
                    "SELECT status FROM am_cash_reservation_event WHERE broker_order_id=? AND source_response_id=?",
                    (broker_order_id, source_response_id),
                ).fetchone()
                if conflict:
                    raise ExecutionError("terminal evidence conflicts with existing cash reservation")
                self._conn.execute(
                    "INSERT INTO am_cash_reservation_event VALUES (?, ?, ?, ?, ?, '0', 'RELEASED', ?, ?)",
                    (str(uuid4()), broker_order_id, sequence + 1, account_id, currency,
                     source_response_id, instant),
                )
        positions = self._conn.execute(
            """SELECT account_id, instrument_id, MAX(sequence_no)
               FROM am_position_reservation_event WHERE broker_order_id=?
               GROUP BY account_id, instrument_id""", (broker_order_id,),
        ).fetchall()
        for account_id, instrument_id, sequence in positions:
            latest = self._conn.execute(
                "SELECT status FROM am_position_reservation_event WHERE broker_order_id=? AND sequence_no=?",
                (broker_order_id, sequence),
            ).fetchone()
            if latest and latest[0] == "RESERVED":
                conflict = self._conn.execute(
                    "SELECT status FROM am_position_reservation_event WHERE broker_order_id=? AND source_response_id=?",
                    (broker_order_id, source_response_id),
                ).fetchone()
                if conflict:
                    raise ExecutionError("terminal evidence conflicts with existing position reservation")
                self._conn.execute(
                    "INSERT INTO am_position_reservation_event VALUES (?, ?, ?, ?, ?, '0', 'RELEASED', ?, ?)",
                    (str(uuid4()), broker_order_id, sequence + 1, account_id, instrument_id,
                     source_response_id, instant),
                )

    def recover_submission_timeout(
        self, *, broker_order_id: str, observed_at_utc: datetime,
        lookup_by_idempotency_key: Callable[[], Mapping[str, object] | None],
        source_response_id: str | None = None,
    ) -> OrderStateEvent:
        """Query before any retry. Absence is ambiguous and remains NO_TRADE."""

        found = lookup_by_idempotency_key()
        if found is None:
            return self._states.append(
                broker_order_id=broker_order_id, state=OrderState.REVIEW_REQUIRED,
                observed_at_utc=observed_at_utc, source_response_id=source_response_id,
                reason="submission timeout; broker acceptance not proven",
            )
        raw_id = found.get("source_response_id") or source_response_id
        if not raw_id:
            raise ExecutionError("timeout recovery requires raw response evidence")
        return self._states.append_broker_state(
            broker_order_id=broker_order_id, raw_state=found.get("status"),
            observed_at_utc=observed_at_utc, source_response_id=str(raw_id),
        )
