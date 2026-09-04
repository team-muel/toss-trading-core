from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from threading import Thread
import time
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error
import urllib.request

import pytest

from asset_management.account.snapshots import AccountTruthRepository
from asset_management.broker.toss_read import TossReadAdapter
from asset_management.broker.rate_limit import PriorityTokenBucket, TokenBucket
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.errors import ReconciliationError, UnknownBrokerState
from asset_management.replay.engine import RawReplayEngine
from asset_management.time.clock import FrozenClock
from toss_trading.broker.credentials import TossCredentials
from toss_trading.broker.toss import TossApiError, TossApiResult, TossReadOnlyAdapter


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, 1, tzinfo=timezone.utc)


def database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript((ROOT / "schemas/asset_management.sql").read_text(encoding="utf-8"))
    return conn


class FakeResponse:
    def __init__(self, body: object, status: int = 200, headers: dict | None = None):
        self.status = status
        self.headers = headers or {"X-RateLimit-Remaining": "4"}
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def toss_transport(conn: sqlite3.Connection) -> TossReadOnlyAdapter:
    moments = iter((NOW, NOW + timedelta(milliseconds=10)))
    return TossReadOnlyAdapter(
        TossCredentials("client", "secret", "internal-account", "https://example.invalid"),
        raw_response_store=SQLiteRawResponseStore(conn),
        now_utc=lambda: next(moments),
    )


def test_real_transport_persists_success_and_health_before_returning():
    conn = database()
    adapter = toss_transport(conn)
    request = urllib.request.Request(
        "https://example.invalid/api/v1/accounts",
        headers={"Authorization": "Bearer secret"},
        method="GET",
    )
    body = {"result": [{"accountSeq": "private", "accountNo": "123456"}]}
    with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
        result = adapter._send(request, "/api/v1/accounts")
    stored = SQLiteRawResponseStore(conn).verified(result.raw_response_id)
    assert stored.requested_at == NOW
    assert stored.received_at == NOW + timedelta(milliseconds=10)
    assert stored.body["result"][0]["accountSeq"] == "***REDACTED***"
    assert conn.execute("SELECT status FROM am_source_health").fetchone()[0] == "OK"


def test_real_transport_persists_http_error_and_blocked_health():
    conn = database()
    adapter = toss_transport(conn)
    request = urllib.request.Request("https://example.invalid/api/v1/accounts", method="GET")
    error = urllib.error.HTTPError(
        request.full_url, 429, "limited", {"Retry-After": "0"}, BytesIO(b'{"error":"limited"}')
    )
    with patch("urllib.request.urlopen", side_effect=error), pytest.raises(TossApiError):
        adapter._send(request, "/api/v1/accounts")
    assert conn.execute("SELECT status_code FROM am_raw_api_response").fetchone()[0] == 429
    assert conn.execute("SELECT status FROM am_source_health").fetchone()[0] == "BLOCKED"


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
            "symbol": "SPY", "currency": "USD", "quantity": self.quantity,
            "lastPrice": "500", "averagePurchasePrice": "490",
            "marketValue": {}, "profitLoss": {}, "cost": {},
        }
        return self._result("/holdings", {"result": {"items": [item]}})

    def get_all_orders(self, status: str):
        order = {
            "orderId": f"{status}-1", "status": self.order_status if status == "OPEN" else "FILLED",
            "quantity": "1",
        }
        return [self._result(f"/orders/{status}", {"result": {"orders": [order], "hasNext": False, "nextCursor": None}})]

    def get_order(self, order_id: str):
        return self._result(f"/orders/{order_id}", {"result": {"orderId": order_id, "execution": {}}})

    def get_buying_power(self, **_query):
        return self._result("/buying-power", {"result": {"currency": "USD", "cashBuyingPower": "1000"}})

    def get_sellable_quantity(self, **_query):
        return self._result("/sellable", {"result": {"sellableQuantity": self.quantity}})

    def get_commissions(self):
        return self._result("/commissions", {"result": [{"marketCountry": "US", "commissionRate": "0.01"}]})

    def get_market_calendar(self, market: str):
        return self._result(f"/calendar/{market}", {"result": {"market": market}})

    def get_stocks(self, symbols: list[str]):
        return self._result("/stocks", {"result": [{"symbol": symbol} for symbol in symbols]})


def test_full_account_truth_collector_covers_every_required_read():
    client = FakeCollectorClient()
    snapshot = TossReadAdapter(client, FrozenClock(NOW)).collect_account_truth(runtime_run_id="run-1")
    assert snapshot.account_id == "account-1"
    assert len(snapshot.open_orders) == 1 and len(snapshot.closed_orders) == 1
    assert len(snapshot.order_details) == 2
    assert len(snapshot.sellable_quantities) == 1 and len(snapshot.commissions) == 1
    assert len(snapshot.market_calendars) == 2 and snapshot.instrument_reference
    assert len(snapshot.raw_response_ids) == len(client.calls)


def test_unknown_broker_status_blocks_account_truth():
    with pytest.raises(UnknownBrokerState):
        TossReadAdapter(
            FakeCollectorClient(order_status="BRAND_NEW"), FrozenClock(NOW)
        ).collect_account_truth(runtime_run_id="run")


def test_repeated_real_collection_conflict_blocks():
    client = FakeCollectorClient()
    adapter = TossReadAdapter(client, FrozenClock(NOW))
    original = adapter.collect_account_truth
    count = 0

    def changing(*, runtime_run_id: str):
        nonlocal count
        count += 1
        client.quantity = str(count)
        return original(runtime_run_id=runtime_run_id)

    adapter.collect_account_truth = changing  # type: ignore[method-assign]
    with pytest.raises(ReconciliationError, match="conflict"):
        adapter.collect_consistent_account_truth(
            first_runtime_run_id="run-1", second_runtime_run_id="run-2"
        )


def test_account_truth_requires_existing_raw_and_replay_verifies_hash():
    conn = database()
    store = SQLiteRawResponseStore(conn)
    raw_id = store.append(
        source="toss", endpoint="/accounts", http_method="GET", request_payload={},
        status_code=200, body={"result": []}, requested_at=NOW, received_at=NOW,
        account_id="account-1", schema_version="1.2.14",
    )
    replay = RawReplayEngine(store).replay((raw_id,))
    assert replay.responses[0].response_hash
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)", ("run", NOW.isoformat(), NOW.isoformat(), "sha", NOW.isoformat()))
    snapshot = TossReadAdapter(FakeCollectorClient(), FrozenClock(NOW)).collect_account_truth(runtime_run_id="run")
    snapshot_id = AccountTruthRepository(conn).append(replace(snapshot, raw_response_ids=(raw_id,)))
    assert conn.execute(
        "SELECT source_response_id FROM am_account_snapshot WHERE account_snapshot_id=?", (snapshot_id,)
    ).fetchone()[0] == raw_id


def test_normalized_account_snapshot_rejects_missing_raw_evidence():
    conn = database()
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)", ("run", NOW.isoformat(), NOW.isoformat(), "sha", NOW.isoformat()))
    client = FakeCollectorClient()
    snapshot = TossReadAdapter(client, FrozenClock(NOW)).collect_account_truth(runtime_run_id="run")
    with pytest.raises(sqlite3.IntegrityError):
        AccountTruthRepository(conn).append(snapshot)


def test_priority_bucket_serves_later_order_state_before_market_data():
    bucket = TokenBucket(capacity=1, refill_per_second=25, tokens=0)
    limiter = PriorityTokenBucket(bucket)
    completed: list[str] = []

    low = Thread(target=lambda: (limiter.acquire(3), completed.append("market")))
    high = Thread(target=lambda: (limiter.acquire(0), completed.append("order")))
    low.start()
    time.sleep(0.005)
    high.start()
    low.join(timeout=1)
    high.join(timeout=1)
    assert completed == ["order", "market"]
