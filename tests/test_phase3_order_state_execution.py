from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from asset_management.account.executions import ExecutionSnapshotRepository
from asset_management.account.orders import OrderStateRepository
from asset_management.domain.enums import OrderState
from asset_management.domain.errors import ExecutionError, ReconciliationError, UnknownBrokerState
from asset_management.execution.fills import OrderObservationService
from asset_management.ledger.posting import ExecutionLedgerPoster, ExecutionPostingContext
from asset_management.ledger.positions import PositionLedger


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, 2, tzinfo=timezone.utc)
SETTLEMENT_DATE = date(2026, 9, 5)


def _raw(conn: sqlite3.Connection, raw_id: str) -> None:
    body = json.dumps({"settlementDate": SETTLEMENT_DATE.isoformat()}, separators=(",", ":"))
    response_hash = hashlib.sha256(body.encode()).hexdigest()
    conn.execute(
        """INSERT INTO am_raw_api_response
           (raw_response_id, source, endpoint, http_method, request_hash, status_code,
            response_hash, body_json, requested_at_utc, received_at_utc, account_id,
            schema_version, headers_json)
           VALUES (?, 'toss', '/api/v1/orders/id', 'GET', ?, 200, ?, ?, ?, ?,
                   'account-1', 'v1', '{}')""",
        (raw_id, f"request-{raw_id}", response_hash, body, NOW.isoformat(), NOW.isoformat()),
    )


def _initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript((ROOT / "schemas/asset_management.sql").read_text(encoding="utf-8"))
    for path in sorted((ROOT / "schemas/migrations").glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def ledger():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _initialize_schema(conn)
    conn.execute(
        """INSERT INTO am_runtime_run VALUES
           ('run-1', ?, ?, 'revision', ?)""",
        (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
    )
    _raw(conn, "raw-order")
    conn.execute(
        """INSERT INTO am_broker_order
           (broker_order_id, runtime_run_id, account_id, status, payload_json,
             source_response_id) VALUES
             ('order-1', 'run-1', 'account-1', 'OPEN',
              '{"symbol":"SPY","side":"BUY","currency":"USD","quantity":"3"}',
              'raw-order')"""
    )
    return conn


def test_three_partial_fills_then_final_fill_produce_exact_nonduplicated_deltas(ledger):
    states = OrderStateRepository(ledger)
    service = OrderObservationService(states, ExecutionSnapshotRepository(ledger))
    fills = [("1", "100"), ("2", "205"), ("2.5", "260"), ("3", "315")]
    deltas = []
    context = ExecutionPostingContext(
        "account-1", "SPY", "BUY", "USD", SETTLEMENT_DATE, "FIFO-v1"
    )
    for index, (quantity, amount) in enumerate(fills, start=1):
        raw_id = f"raw-{index}"
        _raw(ledger, raw_id)
        observed = service.observe(
            broker_order_id="order-1",
            raw_state="FILLED" if index == 4 else "PARTIAL_FILLED",
            observed_at_utc=NOW,
            source_response_id=raw_id,
            execution={
                "filledQuantity": quantity,
                "filledAmount": amount,
                "averageFilledPrice": str(Decimal(amount) / Decimal(quantity)),
                "commission": str(index),
                "tax": "0",
            },
            posting_context=context,
        )
        assert observed.execution_delta is not None
        deltas.append(observed.execution_delta)

    assert [delta.quantity for delta in deltas] == [
        Decimal("1"), Decimal("1"), Decimal("0.5"), Decimal("0.5")
    ]
    assert [delta.amount for delta in deltas] == [
        Decimal("100"), Decimal("105"), Decimal("55"), Decimal("55")
    ]
    assert sum(delta.quantity for delta in deltas) == Decimal("3")
    assert ledger.execute(
        "SELECT SUM(CAST(quantity_delta_decimal AS NUMERIC)) FROM am_position_ledger"
    ).fetchone()[0] == 3
    assert ledger.execute("SELECT COUNT(*) FROM am_execution_posting").fetchone()[0] == 4
    assert states.require_terminal("order-1").state is OrderState.FILLED


def test_allowed_order_lifecycle_path_is_recorded_in_sequence(ledger):
    states = OrderStateRepository(ledger)
    path = (
        OrderState.PLANNED,
        OrderState.SUBMITTING,
        OrderState.SUBMITTED,
        OrderState.ACKNOWLEDGED,
        OrderState.OPEN,
        OrderState.PARTIALLY_FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.CANCELED,
    )
    events = [
        states.append(broker_order_id="order-1", state=state, observed_at_utc=NOW)
        for state in path
    ]
    assert [event.previous_state for event in events] == [None, *path[:-1]]
    assert states.require_terminal("order-1").state is OrderState.CANCELED


def test_repeated_cumulative_snapshot_creates_no_second_delta(ledger):
    _raw(ledger, "raw-1")
    _raw(ledger, "raw-2")
    states = OrderStateRepository(ledger)
    service = OrderObservationService(states, ExecutionSnapshotRepository(ledger))
    values = {"filledQuantity": "1", "filledAmount": "100", "averageFilledPrice": "100"}
    context = ExecutionPostingContext(
        "account-1", "SPY", "BUY", "USD", SETTLEMENT_DATE, "FIFO-v1"
    )
    first = service.observe(
        broker_order_id="order-1", raw_state="PARTIAL_FILLED", observed_at_utc=NOW,
        source_response_id="raw-1", execution=values, posting_context=context,
    )
    second = service.observe(
        broker_order_id="order-1", raw_state="PARTIAL_FILLED", observed_at_utc=NOW,
        source_response_id="raw-2", execution=values, posting_context=context,
    )
    assert first.execution_delta is not None
    assert second.execution_delta is None
    assert ledger.execute("SELECT COUNT(*) FROM am_execution_delta").fetchone()[0] == 1


def test_cumulative_fill_decrease_is_unreconciled(ledger):
    _raw(ledger, "raw-1")
    _raw(ledger, "raw-2")
    repository = ExecutionSnapshotRepository(ledger)
    repository.append(
        broker_order_id="order-1", cumulative_quantity="2", cumulative_amount="200",
        average_price="100", observed_at_utc=NOW, source_response_id="raw-1",
    )
    with pytest.raises(ReconciliationError, match="cannot decrease"):
        repository.append(
            broker_order_id="order-1", cumulative_quantity="1", cumulative_amount="100",
            average_price="100", observed_at_utc=NOW, source_response_id="raw-2",
        )


def test_invalid_transition_and_unknown_state_fail_closed(ledger):
    states = OrderStateRepository(ledger)
    _raw(ledger, "raw-filled")
    states.append_broker_state(broker_order_id="order-1", raw_state="FILLED",
                               observed_at_utc=NOW, source_response_id="raw-filled")
    with pytest.raises(ExecutionError, match="terminal order"):
        states.append(broker_order_id="order-1", state=OrderState.OPEN, observed_at_utc=NOW)

    _raw(ledger, "raw-unknown")
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _initialize_schema(conn)
    conn.execute("INSERT INTO am_runtime_run VALUES ('run-1', ?, ?, 'rev', ?)",
                 (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
    _raw(conn, "raw-unknown")
    conn.execute("""INSERT INTO am_broker_order VALUES
                 ('order-1','run-1','account-1','X',
                  '{"symbol":"SPY","side":"BUY","currency":"USD","quantity":"3"}',
                  'raw-unknown')""")
    unknown_states = OrderStateRepository(conn)
    with pytest.raises(UnknownBrokerState):
        unknown_states.append_broker_state(
            broker_order_id="order-1", raw_state="NEW_VENDOR_VALUE",
            observed_at_utc=NOW, source_response_id="raw-unknown",
        )
    assert unknown_states.latest("order-1").blocks_new_order


def test_timeout_queries_broker_and_never_calls_submit(ledger):
    states = OrderStateRepository(ledger)
    states.append(broker_order_id="order-1", state=OrderState.SUBMITTING, observed_at_utc=NOW)
    service = OrderObservationService(states, ExecutionSnapshotRepository(ledger))
    calls = {"lookup": 0, "submit": 0}

    def lookup():
        calls["lookup"] += 1
        return None

    event = service.recover_submission_timeout(
        broker_order_id="order-1", observed_at_utc=NOW,
        lookup_by_idempotency_key=lookup,
    )
    assert event.state is OrderState.REVIEW_REQUIRED
    assert calls == {"lookup": 1, "submit": 0}
    with pytest.raises(ExecutionError, match="not proven"):
        states.require_terminal("order-1")


def test_cancel_rejected_requires_fresh_original_order_query(ledger):
    states = OrderStateRepository(ledger)
    _raw(ledger, "raw-open")
    states.append_broker_state(broker_order_id="order-1", raw_state="OPEN",
                               observed_at_utc=NOW, source_response_id="raw-open")
    states.append(broker_order_id="order-1", state=OrderState.CANCEL_PENDING, observed_at_utc=NOW)
    _raw(ledger, "raw-rejected")
    review = states.append_broker_state(
        broker_order_id="order-1", raw_state="CANCEL_REJECTED",
        observed_at_utc=NOW, source_response_id="raw-rejected",
    )
    assert review.state is OrderState.REVIEW_REQUIRED
    with pytest.raises(ExecutionError, match="not proven"):
        states.require_terminal("order-1")

    _raw(ledger, "raw-refreshed")
    refreshed = states.append_broker_state(
        broker_order_id="order-1", raw_state="OPEN", observed_at_utc=NOW,
        source_response_id="raw-refreshed",
    )
    assert refreshed.previous_state is OrderState.REVIEW_REQUIRED
    assert refreshed.state is OrderState.OPEN


def test_state_and_execution_tables_are_append_only(ledger):
    _raw(ledger, "raw-1")
    state = OrderStateRepository(ledger).append_broker_state(
        broker_order_id="order-1", raw_state="OPEN", observed_at_utc=NOW,
        source_response_id="raw-1",
    )
    snapshot, delta = ExecutionSnapshotRepository(ledger).append(
        broker_order_id="order-1", cumulative_quantity="1", cumulative_amount="100",
        average_price="100", observed_at_utc=NOW, source_response_id="raw-1",
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.execute("UPDATE am_order_state_event SET state='FILLED' WHERE order_state_event_id=?",
                       (state.order_state_event_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.execute("DELETE FROM am_execution_snapshot WHERE execution_snapshot_id=?",
                       (snapshot.execution_snapshot_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.execute("UPDATE am_execution_delta SET amount_decimal='0' WHERE execution_delta_id=?",
                       (delta.execution_delta_id,))


def test_invalid_filled_observation_rolls_back_state_and_snapshot_together(ledger):
    _raw(ledger, "raw-zero")
    service = OrderObservationService(
        OrderStateRepository(ledger), ExecutionSnapshotRepository(ledger)
    )
    with pytest.raises(ExecutionError, match="positive cumulative fill"):
        service.observe(
            broker_order_id="order-1", raw_state="FILLED", observed_at_utc=NOW,
            source_response_id="raw-zero",
            execution={"filledQuantity": "0", "filledAmount": "0"},
            posting_context=ExecutionPostingContext(
                "account-1", "SPY", "BUY", "USD", SETTLEMENT_DATE, "FIFO-v1"
            ),
        )
    assert ledger.execute("SELECT COUNT(*) FROM am_order_state_event").fetchone()[0] == 0
    assert ledger.execute("SELECT COUNT(*) FROM am_execution_snapshot").fetchone()[0] == 0
    assert ledger.execute("SELECT COUNT(*) FROM am_execution_delta").fetchone()[0] == 0


def test_reposting_same_delta_does_not_duplicate_cash_or_position(ledger):
    _raw(ledger, "raw-fill")
    _, delta = ExecutionSnapshotRepository(ledger).append(
        broker_order_id="order-1", cumulative_quantity="1.25",
        cumulative_amount="125", average_price="100",
        cumulative_commission="0.25", cumulative_tax="0.10",
        observed_at_utc=NOW, source_response_id="raw-fill",
    )
    assert delta is not None
    poster = ExecutionLedgerPoster(ledger)
    context = ExecutionPostingContext(
        "account-1", "SPY", "BUY", "USD", SETTLEMENT_DATE, "FIFO-v1"
    )
    first = poster.post(delta, context, posted_at_utc=NOW)
    second = poster.post(delta, context, posted_at_utc=NOW)
    assert first == second
    assert first.cash_delta == Decimal("-125.35")
    assert first.quantity_delta == Decimal("1.25")
    assert ledger.execute("SELECT COUNT(*) FROM am_execution_posting").fetchone()[0] == 1
    assert ledger.execute("SELECT COUNT(*) FROM am_cash_ledger").fetchone()[0] == 3
    assert ledger.execute("SELECT COUNT(*) FROM am_position_ledger").fetchone()[0] == 1
    components = dict(ledger.execute("SELECT event_type, amount_decimal FROM am_cash_ledger"))
    assert components == {
        "TRADE_COST": "-125", "COMMISSION": "-0.25", "TAX": "-0.10"
    }


def test_fill_state_cannot_exist_without_execution_and_atomic_posting(ledger):
    _raw(ledger, "raw-filled")
    service = OrderObservationService(
        OrderStateRepository(ledger), ExecutionSnapshotRepository(ledger)
    )
    with pytest.raises(ExecutionError, match="requires execution"):
        service.observe(broker_order_id="order-1", raw_state="FILLED",
                        observed_at_utc=NOW, source_response_id="raw-filled")
    assert ledger.execute("SELECT COUNT(*) FROM am_order_state_event").fetchone()[0] == 0


def test_posting_rejects_fabricated_delta_wrong_owner_and_changed_replay_context(ledger):
    _raw(ledger, "raw-fill")
    _, delta = ExecutionSnapshotRepository(ledger).append(
        broker_order_id="order-1", cumulative_quantity="1", cumulative_amount="100",
        average_price="100", observed_at_utc=NOW, source_response_id="raw-fill",
    )
    assert delta is not None
    poster = ExecutionLedgerPoster(ledger)
    with pytest.raises(ReconciliationError, match="persisted delta"):
        poster.post(
            type(delta)(delta.execution_delta_id, delta.broker_order_id,
                        delta.from_snapshot_id, delta.to_snapshot_id,
                        delta.quantity, Decimal("999"), delta.commission, delta.tax),
            ExecutionPostingContext("account-1", "SPY", "BUY", "USD", date(2026, 9, 5), "FIFO-v1"),
            posted_at_utc=NOW,
        )
    with pytest.raises(ReconciliationError, match="broker order identity"):
        poster.post(
            delta,
            ExecutionPostingContext("other-account", "SPY", "BUY", "USD", date(2026, 9, 5), "FIFO-v1"),
            posted_at_utc=NOW,
        )
    original = ExecutionPostingContext(
        "account-1", "SPY", "BUY", "USD", date(2026, 9, 5), "FIFO-v1"
    )
    poster.post(delta, original, posted_at_utc=NOW)
    with pytest.raises(ReconciliationError, match="settlement evidence conflicts"):
        poster.post(
            delta,
            ExecutionPostingContext("account-1", "SPY", "BUY", "USD", date(2026, 9, 6), "FIFO-v1"),
            posted_at_utc=NOW,
        )


def test_internal_initial_and_observation_chronology_are_enforced(ledger):
    states = OrderStateRepository(ledger)
    with pytest.raises(ExecutionError, match="must begin"):
        states.append(broker_order_id="order-1", state=OrderState.OPEN,
                      observed_at_utc=NOW)
    states.append(broker_order_id="order-1", state=OrderState.PLANNED,
                  observed_at_utc=NOW)
    with pytest.raises(ExecutionError, match="cannot move backwards"):
        states.append(broker_order_id="order-1", state=OrderState.SUBMITTING,
                      observed_at_utc=NOW - timedelta(seconds=1))


def test_fill_status_quantity_and_replayed_evidence_must_be_exact(ledger):
    service = OrderObservationService(
        OrderStateRepository(ledger), ExecutionSnapshotRepository(ledger)
    )
    context = ExecutionPostingContext(
        "account-1", "SPY", "BUY", "USD", SETTLEMENT_DATE, "FIFO-v1"
    )
    _raw(ledger, "raw-incomplete-final")
    with pytest.raises(ExecutionError, match="must equal ordered quantity"):
        service.observe(
            broker_order_id="order-1", raw_state="FILLED", observed_at_utc=NOW,
            source_response_id="raw-incomplete-final",
            execution={"filledQuantity": "2", "filledAmount": "200",
                       "averageFilledPrice": "100"},
            posting_context=context,
        )
    assert ledger.execute("SELECT COUNT(*) FROM am_order_state_event").fetchone()[0] == 0

    _raw(ledger, "raw-open")
    states = OrderStateRepository(ledger)
    states.append_broker_state(
        broker_order_id="order-1", raw_state="OPEN", observed_at_utc=NOW,
        source_response_id="raw-open",
    )
    with pytest.raises(ExecutionError, match="replayed evidence conflicts"):
        states.append_broker_state(
            broker_order_id="order-1", raw_state="CANCELED",
            observed_at_utc=NOW, source_response_id="raw-open",
        )


def test_execution_source_evidence_cannot_be_reused_for_changed_values(ledger):
    _raw(ledger, "raw-fill-once")
    snapshots = ExecutionSnapshotRepository(ledger)
    snapshots.append(
        broker_order_id="order-1", cumulative_quantity="1", cumulative_amount="100",
        average_price="100", observed_at_utc=NOW, source_response_id="raw-fill-once",
    )
    with pytest.raises(ReconciliationError, match="replayed evidence conflicts"):
        snapshots.append(
            broker_order_id="order-1", cumulative_quantity="2", cumulative_amount="200",
            average_price="100", observed_at_utc=NOW, source_response_id="raw-fill-once",
        )


def test_tax_lot_and_average_cost_are_point_in_time_and_commission_proportional(ledger):
    positions = PositionLedger(ledger)
    positions.record_opening(
        account_id="account-1", instrument_id="SPY", native_currency="USD",
        as_of_utc=NOW, quantity="1", average_cost="100", evidence="raw-order",
        approved_by="owner", tax_policy_version="FIFO-v1",
    )
    future = NOW + timedelta(days=1)
    _raw(ledger, "raw-future")
    _, delta = ExecutionSnapshotRepository(ledger).append(
        broker_order_id="order-1", cumulative_quantity="1", cumulative_amount="100",
        average_price="100", cumulative_commission="10", observed_at_utc=future,
        source_response_id="raw-future",
    )
    assert delta is not None
    ExecutionLedgerPoster(ledger).post(
        delta,
        ExecutionPostingContext("account-1", "SPY", "BUY", "USD",
                                future.date(), "FIFO-v1"),
        posted_at_utc=future,
    )
    assert positions.state(account_id="account-1", instrument_id="SPY",
                           as_of_utc=NOW).average_cost == Decimal("100")
    future_state = positions.state(account_id="account-1", instrument_id="SPY",
                                   as_of_utc=future)
    assert future_state.quantity == Decimal("2")
    assert future_state.average_cost == Decimal("105")
