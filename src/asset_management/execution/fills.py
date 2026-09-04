"""Fail-closed order observation and cumulative-fill processing."""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping

from asset_management.account.executions import (
    ExecutionDelta,
    ExecutionSnapshot,
    ExecutionSnapshotRepository,
)
from asset_management.account.orders import (
    OrderStateEvent,
    OrderStateRepository,
    normalize_broker_order_state,
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
        # UNKNOWN is itself material evidence. Preserve it before raising fail-closed.
        if normalize_broker_order_state(raw_state) is OrderState.UNKNOWN:
            self._states.append_broker_state(
                broker_order_id=broker_order_id, raw_state=raw_state,
                observed_at_utc=observed_at_utc, source_response_id=source_response_id,
            )
        self._conn.execute("SAVEPOINT am_order_observation")
        try:
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
                posting = (
                    self._poster.post(delta, posting_context, posted_at_utc=observed_at_utc)
                    if delta is not None and posting_context is not None else None
                )
                result = ObservedOrder(state, snapshot, delta, posting)
            self._conn.execute("RELEASE SAVEPOINT am_order_observation")
            return result
        except Exception:
            self._conn.execute("ROLLBACK TO SAVEPOINT am_order_observation")
            self._conn.execute("RELEASE SAVEPOINT am_order_observation")
            raise

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
