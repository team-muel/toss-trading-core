from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import sqlite3
from uuid import uuid4

from asset_management.domain.enums import OrderState
from asset_management.domain.errors import ExecutionError, UnknownBrokerState


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str
    status: str
    source_response_id: str


TERMINAL_ORDER_STATES = frozenset(
    {OrderState.FILLED, OrderState.CANCELED, OrderState.REPLACED, OrderState.REJECTED}
)
REVIEW_ORDER_STATES = frozenset({OrderState.UNKNOWN, OrderState.REVIEW_REQUIRED})

_BROKER_STATE_MAP = {
    "PENDING": OrderState.ACKNOWLEDGED,
    "ACKNOWLEDGED": OrderState.ACKNOWLEDGED,
    "OPEN": OrderState.OPEN,
    "PARTIAL_FILLED": OrderState.PARTIALLY_FILLED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
    "FILLED": OrderState.FILLED,
    "PENDING_CANCEL": OrderState.CANCEL_PENDING,
    "CANCEL_PENDING": OrderState.CANCEL_PENDING,
    "CANCELED": OrderState.CANCELED,
    "CANCELLED": OrderState.CANCELED,
    "PENDING_REPLACE": OrderState.REPLACE_PENDING,
    "REPLACE_PENDING": OrderState.REPLACE_PENDING,
    "REPLACED": OrderState.REPLACED,
    "REJECTED": OrderState.REJECTED,
    "CANCEL_REJECTED": OrderState.REVIEW_REQUIRED,
    "REPLACE_REJECTED": OrderState.REVIEW_REQUIRED,
}

_ALLOWED_TRANSITIONS = {
    OrderState.PLANNED: {OrderState.SUBMITTING},
    OrderState.SUBMITTING: {
        OrderState.SUBMITTED, OrderState.ACKNOWLEDGED, OrderState.OPEN,
        OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.REJECTED,
        OrderState.REVIEW_REQUIRED, OrderState.UNKNOWN,
    },
    OrderState.SUBMITTED: {
        OrderState.ACKNOWLEDGED, OrderState.OPEN, OrderState.PARTIALLY_FILLED,
        OrderState.FILLED, OrderState.CANCEL_PENDING, OrderState.REJECTED,
        OrderState.REVIEW_REQUIRED, OrderState.UNKNOWN,
    },
    OrderState.ACKNOWLEDGED: {
        OrderState.OPEN, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
        OrderState.CANCEL_PENDING, OrderState.CANCELED, OrderState.REJECTED,
        OrderState.REVIEW_REQUIRED, OrderState.UNKNOWN,
    },
    OrderState.OPEN: {
        OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCEL_PENDING,
        OrderState.CANCELED, OrderState.REPLACE_PENDING, OrderState.REJECTED,
        OrderState.REVIEW_REQUIRED, OrderState.UNKNOWN,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.FILLED, OrderState.CANCEL_PENDING, OrderState.CANCELED,
        OrderState.REPLACE_PENDING, OrderState.REVIEW_REQUIRED, OrderState.UNKNOWN,
    },
    OrderState.CANCEL_PENDING: {
        OrderState.CANCELED, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
        OrderState.REVIEW_REQUIRED, OrderState.UNKNOWN,
    },
    OrderState.REPLACE_PENDING: {
        OrderState.REPLACED, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
        OrderState.REVIEW_REQUIRED, OrderState.UNKNOWN,
    },
    OrderState.REVIEW_REQUIRED: {
        OrderState.ACKNOWLEDGED, OrderState.OPEN, OrderState.PARTIALLY_FILLED,
        OrderState.FILLED, OrderState.CANCEL_PENDING, OrderState.CANCELED,
        OrderState.REPLACE_PENDING, OrderState.REPLACED, OrderState.REJECTED,
        OrderState.UNKNOWN,
    },
    OrderState.UNKNOWN: {OrderState.REVIEW_REQUIRED},
}


def normalize_broker_order_state(raw_state: object) -> OrderState:
    value = str(raw_state or "").strip().upper()
    return _BROKER_STATE_MAP.get(value, OrderState.UNKNOWN)


def assert_order_transition(previous: OrderState | None, current: OrderState) -> None:
    if previous is None:
        return
    if previous == current:
        return
    if previous in TERMINAL_ORDER_STATES:
        raise ExecutionError(f"terminal order cannot transition: {previous}->{current}")
    if current not in _ALLOWED_TRANSITIONS.get(previous, set()):
        raise ExecutionError(f"invalid order transition: {previous}->{current}")


@dataclass(frozen=True, slots=True)
class OrderStateEvent:
    order_state_event_id: str
    broker_order_id: str
    previous_state: OrderState | None
    state: OrderState
    observed_at_utc: datetime
    source_response_id: str | None
    reason: str | None

    @property
    def blocks_new_order(self) -> bool:
        return self.state in REVIEW_ORDER_STATES


class OrderStateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def latest(self, broker_order_id: str) -> OrderStateEvent | None:
        row = self._conn.execute(
            """SELECT order_state_event_id, broker_order_id, previous_state, state,
                      observed_at_utc, source_response_id, reason
               FROM am_order_state_event WHERE broker_order_id = ?
               ORDER BY sequence_no DESC LIMIT 1""",
            (broker_order_id,),
        ).fetchone()
        if row is None:
            return None
        return OrderStateEvent(
            str(row[0]), str(row[1]), OrderState(row[2]) if row[2] else None,
            OrderState(row[3]), datetime.fromisoformat(str(row[4])), row[5], row[6],
        )

    def append(
        self,
        *,
        broker_order_id: str,
        state: OrderState,
        observed_at_utc: datetime,
        source_response_id: str | None = None,
        reason: str | None = None,
    ) -> OrderStateEvent:
        if observed_at_utc.tzinfo is None:
            raise ExecutionError("order state timestamp must be timezone-aware")
        previous = self.latest(broker_order_id)
        previous_state = previous.state if previous else None
        assert_order_transition(previous_state, state)
        existing = None
        if source_response_id is not None:
            existing = self._conn.execute(
                "SELECT order_state_event_id FROM am_order_state_event WHERE broker_order_id=? AND source_response_id=?",
                (broker_order_id, source_response_id),
            ).fetchone()
        if existing:
            result = self.latest(broker_order_id)
            if result is None or result.order_state_event_id != existing[0]:
                raise ExecutionError("replayed evidence is not the latest order state")
            return result
        event_id = str(uuid4())
        sequence = self._conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM am_order_state_event WHERE broker_order_id=?",
            (broker_order_id,),
        ).fetchone()[0]
        instant = observed_at_utc.astimezone(timezone.utc)
        self._conn.execute(
            """INSERT INTO am_order_state_event
               (order_state_event_id, broker_order_id, sequence_no, previous_state,
                state, observed_at_utc, source_response_id, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, broker_order_id, sequence, previous_state, state, instant.isoformat(),
             source_response_id, reason),
        )
        return OrderStateEvent(event_id, broker_order_id, previous_state, state, instant,
                               source_response_id, reason)

    def append_broker_state(
        self, *, broker_order_id: str, raw_state: object, observed_at_utc: datetime,
        source_response_id: str,
    ) -> OrderStateEvent:
        state = normalize_broker_order_state(raw_state)
        reason = f"unknown broker order state: {raw_state!r}" if state is OrderState.UNKNOWN else None
        event = self.append(
            broker_order_id=broker_order_id, state=state,
            observed_at_utc=observed_at_utc, source_response_id=source_response_id,
            reason=reason,
        )
        if state is OrderState.UNKNOWN:
            raise UnknownBrokerState(reason)
        return event

    def require_terminal(self, broker_order_id: str) -> OrderStateEvent:
        latest = self.latest(broker_order_id)
        if latest is None:
            raise ExecutionError("order state is missing; submission is blocked")
        if latest.state not in TERMINAL_ORDER_STATES:
            raise ExecutionError(
                f"order final state is not proven ({latest.state}); submission is blocked"
            )
        return latest
