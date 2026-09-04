from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
import sqlite3

import pytest

from asset_management.account.consistency import compare_repeated_reads
from asset_management.broker.contracts import (
    parse_broker_enum,
    require_decimal_string,
    require_pagination,
    require_timestamp,
)
from asset_management.broker.rate_limit import PrioritizedRead, ReadPriority, priority_order
from asset_management.broker.redaction import redact, sanitized_headers
from asset_management.broker.toss_read import TossReadAdapter
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.enums import DataStatus
from asset_management.domain.errors import DataQualityError


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)


class OrderStatus(StrEnum):
    OPEN = "OPEN"
    FILLED = "FILLED"


def database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "schemas/asset_management.sql").read_text(encoding="utf-8"))
    return conn


def test_raw_response_is_redacted_before_append_and_is_immutable():
    conn = database()
    identifier = SQLiteRawResponseStore(conn).append(
        source="toss",
        endpoint="/api/v1/accounts",
        http_method="GET",
        request_payload={"client_secret": "secret", "query": "ok"},
        status_code=200,
        body={"access_token": "token", "accountNo": "12345678", "email": "private@example.com"},
        requested_at=NOW,
        received_at=NOW,
        account_id="internal-account-key",
        schema_version="1.2.14",
        headers={"Authorization": "Bearer token", "X-RateLimit-Remaining": "4"},
    )
    row = conn.execute(
        "SELECT request_hash, response_hash, body_json, headers_json, account_id FROM am_raw_api_response WHERE raw_response_id = ?",
        (identifier,),
    ).fetchone()
    stored_body = json.loads(row[2])
    assert stored_body["access_token"] == "***REDACTED***"
    assert stored_body["accountNo"] == "***REDACTED***"
    assert stored_body["email"] == "***REDACTED***"
    assert "Authorization" not in row[3]
    assert "X-RateLimit-Remaining" in row[3]
    assert row[4] == "internal-account-key"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE am_raw_api_response SET status_code=500 WHERE raw_response_id=?", (identifier,))


def test_recursive_redaction_removes_credentials_and_personal_data():
    payload = redact({"nested": [{"client_secret": "x", "email": "x@y"}], "safe": 1})
    assert payload == {"nested": [{"client_secret": "***REDACTED***", "email": "***REDACTED***"}], "safe": 1}
    assert sanitized_headers({"authorization": "x", "Cookie": "y", "Rate": "1"}) == {"Rate": "1"}


def test_unknown_enum_is_preserved_and_not_coerced():
    parsed = parse_broker_enum("NEW_BROKER_STATE", OrderStatus)
    assert parsed.status is DataStatus.UNKNOWN
    assert parsed.value is None
    assert parsed.raw_value == "NEW_BROKER_STATE"


def test_contract_rejects_invalid_pagination_decimal_and_timestamp():
    with pytest.raises(DataQualityError):
        require_pagination({"hasNext": True, "nextCursor": None}, "/orders")
    with pytest.raises(DataQualityError):
        require_decimal_string(1.2, "amount")
    with pytest.raises(DataQualityError):
        require_timestamp("2026-01-01T00:00:00", "orderedAt")
    assert require_decimal_string("1.20", "amount").as_tuple().exponent == -2


def test_rate_limit_priority_protects_existing_state_reads():
    ordered = priority_order(
        (
            PrioritizedRead(ReadPriority.NEW_MARKET_DATA, "/prices", 0),
            PrioritizedRead(ReadPriority.ACCOUNT_RECONCILIATION, "/holdings", 1),
            PrioritizedRead(ReadPriority.EXISTING_ORDER_STATE, "/orders", 2),
        )
    )
    assert [item.endpoint for item in ordered] == ["/orders", "/holdings", "/prices"]


def test_repeated_read_consistency_detects_account_drift():
    first = {"holdings": [{"symbol": "A", "quantity": "1"}], "orders": []}
    assert compare_repeated_reads(first, first).status is DataStatus.KNOWN
    second = {"holdings": [{"symbol": "A", "quantity": "2"}], "orders": []}
    result = compare_repeated_reads(first, second)
    assert result.status is DataStatus.CONFLICT
    assert result.differing_sections == ("holdings",)


def test_new_toss_boundary_refuses_unpersisted_responses():
    class ClientWithoutLedger:
        ledger = None

    with pytest.raises(ValueError, match="raw-response ledger"):
        TossReadAdapter(ClientWithoutLedger())  # type: ignore[arg-type]


def test_new_toss_boundary_exposes_no_write_operation():
    public = {name for name in dir(TossReadAdapter) if not name.startswith("_")}
    assert {"submit", "cancel", "modify", "place_order"}.isdisjoint(public)
    assert {
        "accounts", "holdings", "orders", "order", "buying_power",
        "sellable_quantity", "commissions", "instrument_reference", "market_calendar",
    } <= public


def test_migration_file_is_replay_safe():
    conn = sqlite3.connect(":memory:")
    sql = (ROOT / "schemas/migrations/0002_toss_read_only.sql").read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.executescript(sql)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"am_raw_api_response", "am_source_health"} <= tables
