from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from asset_management.account.executions import ExecutionSnapshotRepository
from asset_management.domain.errors import DataQualityError, ReconciliationError
from asset_management.ledger.cash import CashEventType, CashLedger, OpenBuyOrder
from asset_management.ledger.positions import PositionLedger
from asset_management.ledger.posting import ExecutionLedgerPoster, ExecutionPostingContext
from asset_management.ledger.replay import LedgerReplay


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, 2, tzinfo=timezone.utc)


def _schema(conn):
    conn.executescript((ROOT / "schemas/asset_management.sql").read_text(encoding="utf-8"))
    for path in sorted((ROOT / "schemas/migrations").glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))


def _raw(conn, raw_id):
    conn.execute(
        """INSERT INTO am_raw_api_response VALUES
           (?, 'toss', '/api/v1/orders/id', 'GET', ?, 200, ?, '{}', ?, ?,
            'account-1', 'v1', '{}')""",
        (raw_id, f"req-{raw_id}", f"res-{raw_id}", NOW.isoformat(), NOW.isoformat()),
    )


@pytest.fixture
def ledger():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _schema(conn)
    conn.execute("INSERT INTO am_runtime_run VALUES ('run-1', ?, ?, 'rev', ?)",
                 (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
    _raw(conn, "raw-order")
    conn.execute("INSERT INTO am_broker_order VALUES ('order-1','run-1','account-1','OPEN','{}','raw-order')")
    return conn


def test_opening_balance_requires_evidence_and_manual_adjustment_requires_approval(ledger):
    cash = CashLedger(ledger)
    with pytest.raises(DataQualityError, match="OPENING_BALANCE_UNKNOWN"):
        cash.record_opening(account_id="account-1", currency="USD", as_of_utc=NOW,
                            opening_balance="100", evidence=None, approved_by=None)
    cash.record_opening(account_id="account-1", currency="USD", as_of_utc=NOW,
                        opening_balance="100", evidence="broker-statement-1", approved_by="owner")
    with pytest.raises(ReconciliationError, match="requires reason"):
        cash.append_event(account_id="account-1", currency="USD", amount="1",
                          event_type=CashEventType.MANUAL_ADJUSTMENT,
                          created_at_utc=NOW, idempotency_key="manual-1")


def test_cash_is_currency_separated_settlement_aware_and_reservation_idempotent(ledger):
    cash = CashLedger(ledger)
    for currency, amount in (("USD", "1000"), ("KRW", "50000")):
        cash.record_opening(account_id="account-1", currency=currency, as_of_utc=NOW,
                            opening_balance=amount, evidence=f"statement-{currency}",
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
                     broker_buying_power_constraint="700")
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
                        opening_balance="1", evidence="statement", approved_by="owner")
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
                        opening_balance="1000", evidence="statement", approved_by="owner")
    positions = PositionLedger(ledger)
    positions.record_opening(account_id="account-1", instrument_id="SPY",
                             native_currency="USD", as_of_utc=NOW, quantity="2",
                             average_cost="90", evidence="statement", approved_by="owner",
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
                             average_cost="100", evidence="statement", approved_by="owner",
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
