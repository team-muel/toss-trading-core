from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from asset_management.account.executions import ExecutionSnapshotRepository
from asset_management.domain.errors import DataQualityError, ReconciliationError
from asset_management.ledger.cash import BrokerConstraint, CashEventType, CashLedger, OpenBuyOrder
from asset_management.ledger.positions import PositionLedger
from asset_management.ledger.posting import ExecutionLedgerPoster, ExecutionPostingContext
from asset_management.ledger.replay import LedgerReplay


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, 2, tzinfo=timezone.utc)


def _schema(conn):
    conn.executescript((ROOT / "schemas/asset_management.sql").read_text(encoding="utf-8"))
    for path in sorted((ROOT / "schemas/migrations").glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))


def _raw(conn, raw_id, body=None):
    if body is None:
        body = {"settlementDate": "2026-09-05"}
    body_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
    response_hash = hashlib.sha256(body_json.encode()).hexdigest()
    conn.execute(
        """INSERT INTO am_raw_api_response VALUES
           (?, 'toss', '/api/v1/orders/id', 'GET', ?, 200, ?, ?, ?, ?,
            'account-1', 'v1', '{}')""",
        (raw_id, f"req-{raw_id}", response_hash, body_json, NOW.isoformat(), NOW.isoformat()),
    )


@pytest.fixture
def ledger():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _schema(conn)
    conn.execute("INSERT INTO am_runtime_run VALUES ('run-1', ?, ?, 'rev', ?)",
                 (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
    _raw(conn, "raw-order")
    conn.execute("""INSERT INTO am_broker_order VALUES
                 ('order-1','run-1','account-1','OPEN',
                  '{"symbol":"SPY","side":"SELL","currency":"USD","quantity":"1"}',
                  'raw-order')""")
    return conn


def test_opening_balance_requires_evidence_and_manual_adjustment_requires_approval(ledger):
    cash = CashLedger(ledger)
    with pytest.raises(DataQualityError, match="OPENING_BALANCE_UNKNOWN"):
        cash.record_opening(account_id="account-1", currency="USD", as_of_utc=NOW,
                            opening_balance="100", evidence=None, approved_by=None)
    cash.record_opening(account_id="account-1", currency="USD", as_of_utc=NOW,
                        opening_balance="100", evidence="raw-order", approved_by="owner")
    with pytest.raises(ReconciliationError, match="requires reason"):
        cash.append_event(account_id="account-1", currency="USD", amount="1",
                          event_type=CashEventType.MANUAL_ADJUSTMENT,
                          created_at_utc=NOW, idempotency_key="manual-1")


def test_cash_is_currency_separated_settlement_aware_and_reservation_idempotent(ledger):
    cash = CashLedger(ledger)
    for currency, amount in (("USD", "1000"), ("KRW", "50000")):
        cash.record_opening(account_id="account-1", currency=currency, as_of_utc=NOW,
                            opening_balance=amount, evidence="raw-order",
                            approved_by="owner")
    cash.append_event(account_id="account-1", currency="USD", amount="100",
                      event_type=CashEventType.DEPOSIT, created_at_utc=NOW,
                      settlement_date=date(2026, 9, 5), idempotency_key="deposit-1")
    _raw(ledger, "raw-reserve")
    order = OpenBuyOrder("order-1", "account-1", "USD",
                         remaining_quantity=Decimal("2"), limit_price=Decimal("100"),
                         expected_fees=Decimal("1"))
    first = cash.reserve_open_order(order, source_response_id="raw-reserve", observed_at_utc=NOW)
    second = cash.reserve_open_order(order, source_response_id="raw-reserve", observed_at_utc=NOW)
    assert first == second
    usd = cash.state(account_id="account-1", currency="USD", as_of_utc=NOW,
                     broker_buying_power_constraint=BrokerConstraint(
                         Decimal("700"), NOW, NOW + timedelta(minutes=1), "raw-reserve"
                     ))
    krw = cash.state(account_id="account-1", currency="KRW", as_of_utc=NOW)
    assert (usd.settled_cash, usd.unsettled_cash, usd.reserved_cash,
            usd.available_cash, usd.orderable_cash) == (
        Decimal("1000"), Decimal("100"), Decimal("201"), Decimal("799"), Decimal("700")
    )
    assert krw.settled_cash == Decimal("50000")
    assert ledger.execute("SELECT COUNT(*) FROM am_cash_reservation_event").fetchone()[0] == 1


def test_fractional_reservations_remain_exact_decimal(ledger):
    cash = CashLedger(ledger)
    cash.record_opening(account_id="account-1", currency="USD", as_of_utc=NOW,
                        opening_balance="1", evidence="raw-order", approved_by="owner")
    _raw(ledger, "raw-r1")
    cash.reserve_open_order(
        OpenBuyOrder("order-1", "account-1", "USD", remaining_amount=Decimal("0.1")),
        source_response_id="raw-r1", observed_at_utc=NOW,
    )
    assert cash.state(account_id="account-1", currency="USD", as_of_utc=NOW).reserved_cash == Decimal("0.1")


def test_open_buy_with_unknown_price_and_amount_is_blocked(ledger):
    _raw(ledger, "raw-reserve")
    with pytest.raises(DataQualityError, match="OPEN_ORDER_PRICE_OR_AMOUNT_UNKNOWN"):
        CashLedger(ledger).reserve_open_order(
            OpenBuyOrder("order-1", "account-1", "USD", remaining_quantity=Decimal("1")),
            source_response_id="raw-reserve", observed_at_utc=NOW,
        )


def test_position_settlement_sell_reservation_tax_lot_and_replay(ledger):
    cash = CashLedger(ledger)
    cash.record_opening(account_id="account-1", currency="USD", as_of_utc=NOW,
                        opening_balance="1000", evidence="raw-order", approved_by="owner")
    positions = PositionLedger(ledger)
    positions.record_opening(account_id="account-1", instrument_id="SPY",
                             native_currency="USD", as_of_utc=NOW, quantity="2",
                             average_cost="90", evidence="raw-order", approved_by="owner",
                             tax_policy_version="FIFO-v1")
    _raw(ledger, "raw-reserve")
    positions.reserve_sell(broker_order_id="order-1", account_id="account-1",
                           instrument_id="SPY", quantity="0.5",
                           source_response_id="raw-reserve", observed_at_utc=NOW)
    _raw(ledger, "raw-fill")
    _, delta = ExecutionSnapshotRepository(ledger).append(
        broker_order_id="order-1", cumulative_quantity="1", cumulative_amount="100",
        average_price="100", observed_at_utc=NOW, source_response_id="raw-fill",
    )
    assert delta is not None
    ExecutionLedgerPoster(ledger).post(
        delta, ExecutionPostingContext("account-1", "SPY", "SELL", "USD",
                                       date(2026, 9, 5), "FIFO-v1"),
        posted_at_utc=NOW,
    )
    state = positions.state(account_id="account-1", instrument_id="SPY", as_of_utc=NOW)
    assert (state.quantity, state.settled_quantity, state.unsettled_quantity,
            state.reserved_sell_quantity, state.available_to_sell) == (
        Decimal("1"), Decimal("2"), Decimal("-1"), Decimal("0.5"), Decimal("1.5")
    )
    lot = ledger.execute("SELECT quantity_decimal FROM am_tax_lot_disposal").fetchone()
    assert Decimal(lot[0]) == Decimal("1")
    replay = LedgerReplay(ledger).rebuild(account_id="account-1", as_of_utc=NOW,
                                          currencies=("USD",), instruments=("SPY",))
    assert replay.cash[0] == cash.state(account_id="account-1", currency="USD", as_of_utc=NOW)
    assert replay.positions[0] == state


def test_sell_reservation_above_settled_quantity_blocks(ledger):
    positions = PositionLedger(ledger)
    positions.record_opening(account_id="account-1", instrument_id="SPY",
                             native_currency="USD", as_of_utc=NOW, quantity="1",
                             average_cost="100", evidence="raw-order", approved_by="owner",
                             tax_policy_version="FIFO-v1")
    _raw(ledger, "raw-reserve")
    positions.reserve_sell(broker_order_id="order-1", account_id="account-1",
                           instrument_id="SPY", quantity="2",
                           source_response_id="raw-reserve", observed_at_utc=NOW)
    with pytest.raises(ReconciliationError, match="exceeds settled"):
        positions.state(account_id="account-1", instrument_id="SPY", as_of_utc=NOW)


def test_execution_posting_requires_settlement_date():
    with pytest.raises(ReconciliationError, match="settlement date"):
        ExecutionPostingContext("account-1", "SPY", "BUY", "USD", None, "FIFO-v1")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "body, message",
    [
        ({}, "evidence is missing"),
        (
            {"settlementDate": "2026-09-05", "nested": {"settlement_date": "2026-09-06"}},
            "conflicts within raw response",
        ),
        ({"settlementDate": "09/05/2026"}, "evidence is malformed"),
    ],
)
def test_execution_posting_fails_closed_on_missing_conflicting_or_malformed_settlement(
    ledger, body, message
):
    _raw(ledger, "raw-bad-settlement", body)
    _, delta = ExecutionSnapshotRepository(ledger).append(
        broker_order_id="order-1", cumulative_quantity="1", cumulative_amount="100",
        average_price="100", observed_at_utc=NOW,
        source_response_id="raw-bad-settlement",
    )
    assert delta is not None
    with pytest.raises(ReconciliationError, match=message):
        ExecutionLedgerPoster(ledger).post(
            delta,
            ExecutionPostingContext(
                "account-1", "SPY", "SELL", "USD", date(2026, 9, 5), "FIFO-v1"
            ),
            posted_at_utc=NOW,
        )
    assert ledger.execute("SELECT COUNT(*) FROM am_execution_posting").fetchone()[0] == 0


def test_database_rejects_settlement_evidence_not_present_in_fill_response(ledger):
    _raw(ledger, "raw-settlement-guard")
    _, delta = ExecutionSnapshotRepository(ledger).append(
        broker_order_id="order-1", cumulative_quantity="1", cumulative_amount="100",
        average_price="100", observed_at_utc=NOW,
        source_response_id="raw-settlement-guard",
    )
    assert delta is not None
    snapshot_id, response_hash = ledger.execute(
        """SELECT d.to_snapshot_id, raw.response_hash
           FROM am_execution_delta d
           JOIN am_execution_snapshot s ON s.execution_snapshot_id=d.to_snapshot_id
           JOIN am_raw_api_response raw ON raw.raw_response_id=s.source_response_id
           WHERE d.execution_delta_id=?""",
        (delta.execution_delta_id,),
    ).fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="raw lineage"):
        ledger.execute(
            """INSERT INTO am_execution_settlement_evidence VALUES
               (?, ?, 'raw-settlement-guard', '2026-09-06', '[\"$.settlementDate\"]', ?, ?)""",
            (delta.execution_delta_id, snapshot_id, response_hash, "0" * 64),
        )


def test_terminal_order_atomically_releases_cash_reservation(ledger):
    cash = CashLedger(ledger)
    cash.record_opening(account_id="account-1", currency="USD", as_of_utc=NOW,
                        opening_balance="1000", evidence="raw-order", approved_by="owner")
    _raw(ledger, "raw-reserve")
    cash.reserve_open_order(
        OpenBuyOrder("order-1", "account-1", "USD", remaining_amount=Decimal("100")),
        source_response_id="raw-reserve", observed_at_utc=NOW,
    )
    _raw(ledger, "raw-terminal")
    from asset_management.account.orders import OrderStateRepository
    from asset_management.execution.fills import OrderObservationService
    OrderObservationService(
        OrderStateRepository(ledger), ExecutionSnapshotRepository(ledger)
    ).observe(
        broker_order_id="order-1", raw_state="CANCELED", observed_at_utc=NOW,
        source_response_id="raw-terminal",
    )
    assert cash.state(account_id="account-1", currency="USD",
                      as_of_utc=NOW).reserved_cash == 0


def test_missing_position_settlement_and_stale_constraint_fail_closed(ledger):
    positions = PositionLedger(ledger)
    positions.record_opening(account_id="account-1", instrument_id="SPY",
                             native_currency="USD", as_of_utc=NOW, quantity="1",
                             average_cost="100", evidence="raw-order", approved_by="owner",
                             tax_policy_version="FIFO-v1")
    ledger.execute("""INSERT INTO am_position_ledger VALUES
                   ('position-x', NULL, 'account-1', 'SPY', '0.1', 'TEST', ?)""",
                   (NOW.isoformat(),))
    with pytest.raises(ReconciliationError, match="settlement evidence"):
        positions.state(account_id="account-1", instrument_id="SPY", as_of_utc=NOW)
    cash = CashLedger(ledger)
    cash.record_opening(account_id="account-1", currency="USD", as_of_utc=NOW,
                        opening_balance="100", evidence="raw-order", approved_by="owner")
    with pytest.raises(ReconciliationError, match="future or stale"):
        cash.state(
            account_id="account-1", currency="USD", as_of_utc=NOW,
            broker_buying_power_constraint=BrokerConstraint(
                Decimal("50"), NOW - timedelta(minutes=2), NOW - timedelta(minutes=1),
                "raw-order",
            ),
        )


def test_direct_manual_adjustment_without_authorization_is_blocked_by_db(ledger):
    with pytest.raises(sqlite3.IntegrityError, match="prior authorization"):
        ledger.execute(
            "INSERT INTO am_cash_ledger VALUES ('manual-x',NULL,'account-1','USD','1',NULL,'MANUAL_ADJUSTMENT',?)",
            (NOW.isoformat(),),
        )


def test_nonexistent_opening_evidence_and_unposted_delta_block_replay(ledger):
    with pytest.raises(DataQualityError, match="EVIDENCE_MISSING"):
        CashLedger(ledger).record_opening(
            account_id="account-1", currency="USD", as_of_utc=NOW,
            opening_balance="100", evidence="fabricated-statement", approved_by="owner",
        )
    CashLedger(ledger).record_opening(
        account_id="account-1", currency="USD", as_of_utc=NOW,
        opening_balance="100", evidence="raw-order", approved_by="owner",
    )
    _raw(ledger, "raw-unposted")
    ExecutionSnapshotRepository(ledger).append(
        broker_order_id="order-1", cumulative_quantity="1", cumulative_amount="100",
        average_price="100", observed_at_utc=NOW, source_response_id="raw-unposted",
    )
    with pytest.raises(ReconciliationError, match="missing ledger posting"):
        LedgerReplay(ledger).rebuild(account_id="account-1", as_of_utc=NOW)


def test_cash_and_reservation_idempotency_conflicts_fail_closed(ledger):
    cash = CashLedger(ledger)
    cash.record_opening(
        account_id="account-1", currency="USD", as_of_utc=NOW,
        opening_balance="1000", evidence="raw-order", approved_by="owner",
    )
    cash.append_event(
        account_id="account-1", currency="USD", amount="10",
        event_type=CashEventType.DEPOSIT, created_at_utc=NOW,
        idempotency_key="deposit-exact",
    )
    with pytest.raises(ReconciliationError, match="idempotency key conflicts"):
        cash.append_event(
            account_id="account-1", currency="USD", amount="11",
            event_type=CashEventType.DEPOSIT, created_at_utc=NOW,
            idempotency_key="deposit-exact",
        )

    _raw(ledger, "raw-reservation-exact")
    order = OpenBuyOrder(
        "order-1", "account-1", "USD", remaining_amount=Decimal("100")
    )
    cash.reserve_open_order(
        order, source_response_id="raw-reservation-exact", observed_at_utc=NOW,
    )
    with pytest.raises(ReconciliationError, match="conflicts with existing cash"):
        cash.reserve_open_order(
            OpenBuyOrder("order-1", "account-1", "USD",
                         remaining_amount=Decimal("99")),
            source_response_id="raw-reservation-exact", observed_at_utc=NOW,
        )
    _raw(ledger, "raw-reservation-next")
    with pytest.raises(ReconciliationError, match="identity cannot change"):
        cash.reserve_open_order(
            OpenBuyOrder("order-1", "account-1", "KRW",
                         remaining_amount=Decimal("100")),
            source_response_id="raw-reservation-next", observed_at_utc=NOW,
        )


def test_replay_verifies_raw_hash_before_reconstructing(ledger):
    CashLedger(ledger).record_opening(
        account_id="account-1", currency="USD", as_of_utc=NOW,
        opening_balance="100", evidence="raw-order", approved_by="owner",
    )
    bad_raw = "raw-bad-hash"
    ledger.execute(
        """INSERT INTO am_raw_api_response VALUES
           (?, 'toss', '/api/v1/orders/id', 'GET', 'request', 200, 'wrong', '{}',
            ?, ?, 'account-1', 'v1', '{}')""",
        (bad_raw, NOW.isoformat(), NOW.isoformat()),
    )
    ledger.execute(
        """INSERT INTO am_cash_reservation_event VALUES
           ('bad-reservation', 'order-1', 1, 'account-1', 'USD', '1',
            'RESERVED', ?, ?)""",
        (bad_raw, NOW.isoformat()),
    )
    with pytest.raises(ReconciliationError, match="raw evidence verification failed"):
        LedgerReplay(ledger).rebuild(account_id="account-1", as_of_utc=NOW)


def test_v6_migration_backfills_only_immutable_v5_evidence():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript((ROOT / "schemas/asset_management.sql").read_text(encoding="utf-8"))
    for path in sorted((ROOT / "schemas/migrations").glob("000[2-5]_*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.execute(
        """INSERT INTO am_position_opening_balance VALUES
           ('position-v5','account-1','SPY','USD',?,'1','100','statement','owner')""",
        (NOW.isoformat(),),
    )
    conn.execute(
        """INSERT INTO am_tax_lot VALUES
           ('opening:position-v5',NULL,'account-1','SPY','2026-09-04','2026-09-04',
            '1','100','0','USD','1','FIFO-v1')"""
    )
    conn.execute(
        """INSERT INTO am_cash_ledger VALUES
           ('manual-v5',NULL,'account-1','USD','1',NULL,'MANUAL_ADJUSTMENT',?)""",
        (NOW.isoformat(),),
    )
    conn.execute(
        """INSERT INTO am_cash_event_metadata VALUES
           ('manual-v5','manual-v5-key','documented correction','owner')"""
    )
    conn.executescript(
        (ROOT / "schemas/migrations/0006_phase3_4_ledger_hardening.sql").read_text(
            encoding="utf-8"
        )
    )
    assert conn.execute(
        "SELECT observed_at_utc FROM am_tax_lot_timing WHERE lot_id='opening:position-v5'"
    ).fetchone() == (NOW.isoformat(),)
    assert conn.execute(
        """SELECT reason, approved_by FROM am_manual_cash_authorization
           WHERE cash_event_id='manual-v5'"""
    ).fetchone() == ("documented correction", "owner")
