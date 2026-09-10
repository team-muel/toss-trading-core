from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.errors import DataQualityError, ReconciliationError
from asset_management.ledger import CashLedger, OpenBuyOrder, cash_state_from_buying_power
from asset_management.broker.toss_read import TossReadAdapter
from asset_management.time.clock import FrozenClock
from asset_management.toss.broker.toss import TossApiResult


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, 2, tzinfo=timezone.utc)


def _schema(conn):
    conn.executescript((ROOT / "schemas/asset_management.sql").read_text(encoding="utf-8"))
    for path in sorted((ROOT / "schemas/migrations").glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))


def _seed_raw_order(conn):
    body_json = json.dumps({"settlementDate": "2026-09-05"}, sort_keys=True, separators=(",", ":"))
    response_hash = hashlib.sha256(body_json.encode()).hexdigest()
    conn.execute(
        """INSERT INTO am_raw_api_response VALUES
           ('raw-order', 'toss', '/api/v1/orders/id', 'GET', 'req-raw-order', 200, ?, ?, ?, ?,
            'account-1', 'v1', '{}')""",
        (response_hash, body_json, NOW.isoformat(), NOW.isoformat()),
    )


@pytest.fixture
def ledger():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _schema(conn)
    conn.execute(
        "INSERT INTO am_runtime_run VALUES ('run-1', ?, ?, 'rev', ?)",
        (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
    )
    _seed_raw_order(conn)
    conn.execute(
        """INSERT INTO am_broker_order VALUES
        ('order-1','run-1','account-1','OPEN',
         '{"symbol":"SPY","side":"SELL","currency":"USD","quantity":"1"}',
         'raw-order')"""
    )
    yield conn
    conn.close()


class FakeCollectorClient:
    ledger = object()
    raw_response_store = None

    def __init__(self, *, quantity: str = "2", order_status: str = "PENDING"):
        self.credentials = SimpleNamespace(account_seq="account-1")
        self.quantity = quantity
        self.order_status = order_status
        self.calls: list[str] = []
        self.counter = 0

    def _result(self, endpoint: str, body: object) -> TossApiResult:
        self.calls.append(endpoint)
        self.counter += 1
        return TossApiResult(endpoint, 200, body, f"raw-{self.counter}")

    def get_accounts(self):
        return self._result("/accounts", {"result": [{"accountSeq": "account-1"}]})

    def get_holdings(self):
        item = {
            "symbol": "SPY",
            "currency": "USD",
            "quantity": self.quantity,
            "lastPrice": "500",
            "averagePurchasePrice": "490",
            "marketValue": {},
            "profitLoss": {},
            "cost": {},
        }
        return self._result("/holdings", {"result": {"items": [item]}})

    def get_all_orders(self, status: str):
        order = {
            "orderId": f"{status}-1",
            "status": self.order_status if status == "OPEN" else "FILLED",
            "quantity": "1",
        }
        return [
            self._result(
                f"/orders/{status}",
                {"result": {"orders": [order], "hasNext": False, "nextCursor": None}},
            )
        ]

    def get_order(self, order_id: str):
        return self._result(
            f"/orders/{order_id}",
            {"result": {"orderId": order_id, "execution": {}}},
        )

    def get_buying_power(self, **_query):
        return self._result(
            "/buying-power",
            {"result": {"currency": "USD", "cashBuyingPower": "1000"}},
        )

    def get_sellable_quantity(self, **_query):
        return self._result(
            "/sellable",
            {"result": {"sellableQuantity": self.quantity}},
        )

    def get_commissions(self):
        return self._result(
            "/commissions",
            {"result": [{"marketCountry": "US", "commissionRate": "0.01"}]},
        )

    def get_market_calendar(self, market: str):
        return self._result(f"/calendar/{market}", {"result": {"market": market}})

    def get_stocks(self, symbols: list[str]):
        return self._result(
            "/stocks",
            {"result": [{"symbol": symbol} for symbol in symbols]},
        )


def raw(conn, **changes):
    values = dict(
        source="toss",
        endpoint="/api/v1/buying-power",
        http_method="GET",
        request_payload={"currency": "USD"},
        status_code=200,
        body={"result": {"currency": "USD", "cashBuyingPower": "700"}},
        requested_at=NOW,
        received_at=NOW,
        account_id="account-1",
        schema_version="1.2.15",
    )
    values.update(changes)
    return SQLiteRawResponseStore(conn).append(**values)


def calculate(conn, source, **changes):
    args = dict(
        source_response_id=source,
        account_id="account-1",
        currency="USD",
        as_of_utc=NOW,
        max_age=timedelta(seconds=60),
        operational_liquidity_reserve=D(100),
        policy_version="cash-policy@1",
        provider_contract_version="1.2.15",
    )
    args.update(changes)
    return cash_state_from_buying_power(conn, **args)


def opening(conn):
    CashLedger(conn).record_opening(
        account_id="account-1",
        currency="USD",
        as_of_utc=NOW,
        opening_balance="1000",
        evidence="raw-order",
        approved_by="fixture-owner",
    )


def test_verified_constraint_reuses_cash_and_reservations_without_changing_assets(ledger):
    opening(ledger)
    cash = CashLedger(ledger)
    cash.reserve_open_order(
        OpenBuyOrder("order-1", "account-1", "USD", remaining_amount=D(200)),
        source_response_id="raw-order",
        observed_at_utc=NOW,
    )
    source = raw(ledger)
    result = calculate(ledger, source, operational_liquidity_reserve=D(150))
    assert D(result["settled_cash"]) == 1000
    assert D(result["reserved_cash"]) == 200
    assert D(result["internal_free_cash"]) == 650
    assert D(result["broker_buying_power"]) == 700
    assert D(result["cash_constrained_buy_limit"]) == 650
    assert cash.state(account_id="account-1", currency="USD", as_of_utc=NOW).settled_cash == 1000
    assert calculate(ledger, source, operational_liquidity_reserve=D(150)) == result
    schema = json.loads(
        (ROOT / "schemas/cash_constraint_evidence.schema.json").read_text()
    )
    assert set(schema["required"]) == set(result)


def test_buying_power_cannot_supply_missing_opening_cash(ledger):
    with pytest.raises(DataQualityError, match="OPENING_BALANCE_UNKNOWN"):
        calculate(ledger, raw(ledger))


@pytest.mark.parametrize(
    "changes",
    [
        {"account_id": "another-account"},
        {"source": "other"},
        {"endpoint": "/api/v1/holdings"},
        {"status_code": 500},
        {"schema_version": "unknown"},
    ],
)
def test_raw_source_context_must_match(ledger, changes):
    with pytest.raises(ReconciliationError, match="SOURCE_CONTEXT_MISMATCH"):
        calculate(ledger, raw(ledger, **changes))


def test_write_response_is_already_rejected_at_raw_store_boundary(ledger):
    with pytest.raises(sqlite3.IntegrityError, match="read-only raw store"):
        raw(ledger, http_method="POST")


def test_corrupted_body_hash_cannot_be_used_as_constraint(ledger):
    source = raw(ledger)
    ledger.execute(
        """INSERT INTO am_raw_api_response
        SELECT 'corrupt', source, endpoint, http_method, request_hash, status_code,
               ?, body_json, requested_at_utc, received_at_utc, account_id, schema_version, headers_json
        FROM am_raw_api_response WHERE raw_response_id=?""",
        ("0" * 64, source),
    )
    with pytest.raises(ReconciliationError, match="RAW_EVIDENCE_INVALID"):
        calculate(ledger, "corrupt")


@pytest.mark.parametrize("value", ["0", "700"])
def test_broker_constraint_can_reduce_but_never_increase_cash(ledger, value):
    opening(ledger)
    source = raw(ledger, body={"result": {"currency": "USD", "cashBuyingPower": value}})
    result = calculate(ledger, source)
    assert D(result["internal_free_cash"]) == 900
    assert D(result["cash_constrained_buy_limit"]) == D(value)


@pytest.mark.parametrize("value", [None, 12.5, "NaN", "Infinity", "-1"])
def test_invalid_or_negative_constraint_fails_closed(ledger, value):
    with pytest.raises(ReconciliationError, match="CASH_CONSTRAINT_"):
        calculate(
            ledger,
            raw(ledger, body={"result": {"currency": "USD", "cashBuyingPower": value}}),
        )


def test_wrong_currency_stale_future_and_delayed_receipt_are_rejected(ledger):
    with pytest.raises(ReconciliationError, match="CURRENCY_MISMATCH"):
        calculate(
            ledger,
            raw(ledger, body={"result": {"currency": "KRW", "cashBuyingPower": "700"}}),
        )
    source = raw(ledger)
    for instant in (NOW - timedelta(seconds=1), NOW + timedelta(seconds=61)):
        with pytest.raises(ReconciliationError, match="FUTURE_OR_STALE"):
            calculate(ledger, source, as_of_utc=instant)
    source = raw(ledger, requested_at=NOW - timedelta(seconds=61))
    with pytest.raises(ReconciliationError, match="FUTURE_OR_STALE"):
        calculate(ledger, source)


def test_missing_raw_invalid_policy_and_insufficient_reserve_rejected(ledger):
    opening(ledger)
    with pytest.raises(ReconciliationError, match="RAW_EVIDENCE_INVALID"):
        calculate(ledger, "missing")
    source = raw(ledger)
    with pytest.raises(ReconciliationError, match="TIME_POLICY_INVALID"):
        calculate(ledger, source, max_age=timedelta(0))
    for reserve in (D(-1), D("NaN"), 1.5):
        with pytest.raises(ReconciliationError, match="RESERVE_INVALID"):
            calculate(ledger, source, operational_liquidity_reserve=reserve)
    with pytest.raises(ReconciliationError, match="RESERVE_EXCEEDS_AVAILABLE"):
        calculate(ledger, source, operational_liquidity_reserve=D(1001))


def test_live_collection_rejects_wrong_currency_and_negative_buying_power():
    for row in (
        {"currency": "KRW", "cashBuyingPower": "700"},
        {"currency": "USD", "cashBuyingPower": "-1"},
    ):
        client = FakeCollectorClient()
        client.get_buying_power = lambda **query: client._result(
            "/buying-power", {"result": row}
        )
        adapter = TossReadAdapter(client, FrozenClock(NOW))
        with pytest.raises(DataQualityError, match="CASH_CONSTRAINT_"):
            adapter.collect_account_truth(runtime_run_id="fixture-run")
        with pytest.raises(DataQualityError, match="CASH_CONSTRAINT_"):
            adapter.buying_power("USD")
