from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from asset_management.account.snapshots import AccountTruthRepository, AccountTruthSnapshot
from asset_management.account.executions import ExecutionSnapshotRepository
from asset_management.account.orders import OrderStateRepository
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.errors import ReconciliationError
from asset_management.ledger.reconciliation import (
    AccountReconciler,
    ReconciliationFact,
    ReconciliationPolicy,
    ReconciliationStatus,
    ReconciliationTarget,
    ToleranceRule,
)
from asset_management.ledger.cash import CashLedger, OpenBuyOrder
from asset_management.ledger.positions import PositionLedger


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, 3, tzinfo=timezone.utc)


def database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript((ROOT / "schemas/asset_management.sql").read_text(encoding="utf-8"))
    for path in sorted((ROOT / "schemas/migrations").glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    return conn


def snapshot(conn: sqlite3.Connection, sequence: int, observed_at: datetime) -> tuple[str, str]:
    runtime_id = f"run-{sequence}"
    conn.execute(
        "INSERT INTO am_runtime_run VALUES (?, ?, ?, 'revision', ?)",
        (runtime_id, observed_at.isoformat(), observed_at.isoformat(), observed_at.isoformat()),
    )
    raw_id = SQLiteRawResponseStore(conn).append(
        source="toss", endpoint="/api/v1/accounts", http_method="GET",
        request_payload={}, status_code=200,
        body={"result": [{"accountSeq": "redacted"}]},
        requested_at=observed_at, received_at=observed_at,
        account_id="account-1", schema_version="1.2.14",
    )
    truth = AccountTruthSnapshot(
        runtime_id, "account-1", observed_at, (), (), (), (), (), (), (), (), (), (),
        (raw_id,),
    )
    return runtime_id, AccountTruthRepository(conn).append(truth)


def policy() -> ReconciliationPolicy:
    numeric_targets = (
        ReconciliationTarget.HOLDINGS, ReconciliationTarget.CASH,
        ReconciliationTarget.OPEN_ORDERS,
        ReconciliationTarget.CUMULATIVE_EXECUTION, ReconciliationTarget.COMMISSION,
        ReconciliationTarget.TAX, ReconciliationTarget.SELLABLE_QUANTITY,
        ReconciliationTarget.BUYING_POWER,
    )
    return ReconciliationPolicy(
        "account-reconciliation-v1", NOW - timedelta(days=1), None,
        "risk-owner", "initial explicit reconciliation tolerances",
        tuple(ToleranceRule(target, Decimal("0")) for target in numeric_targets),
    )


def facts(*, holding: str = "2") -> tuple[ReconciliationFact, ...]:
    F, T = ReconciliationFact, ReconciliationTarget
    return (
        F(T.HOLDINGS, "SPY:quantity", holding, True, "USD", "SPY"),
        F(T.CASH, "USD", "1000", True, "USD"),
        F(T.OPEN_ORDERS, "*", "NONE", False),
        F(T.ORDER_STATE, "*", "NONE", False),
        F(T.CUMULATIVE_EXECUTION, "*", "0", True),
        F(T.COMMISSION, "*", "0", True, "USD"),
        F(T.TAX, "*", "0", True, "USD"),
        F(T.SETTLEMENT_DATE, "*", "NONE", False),
        F(T.SELLABLE_QUANTITY, "SPY", holding, True, None, "SPY"),
        F(T.BUYING_POWER, "USD", "1000", True, "USD"),
    )


def replace_fact(
    values: tuple[ReconciliationFact, ...], target: ReconciliationTarget,
    dimension_key: str, value: str,
) -> tuple[ReconciliationFact, ...]:
    return tuple(
        ReconciliationFact(
            item.target, item.dimension_key, value, item.numeric,
            item.currency, item.instrument_id,
        ) if (item.target, item.dimension_key) == (target, dimension_key) else item
        for item in values
    )


def test_all_targets_match_and_gate_allows_only_latest_complete_run():
    conn = database()
    runtime_id, snapshot_id = snapshot(conn, 1, NOW)
    run = AccountReconciler(conn).reconcile(
        runtime_run_id=runtime_id, account_snapshot_id=snapshot_id,
        account_id="account-1", as_of_utc=NOW, policy=policy(),
        broker_facts=facts(), internal_facts=facts(),
    )
    assert run.status is ReconciliationStatus.MATCH
    assert {item.target for item in run.items} == set(ReconciliationTarget)
    assert AccountReconciler(conn).trade_gate(
        account_id="account-1", reconciliation_run_id=run.reconciliation_run_id
    ).action == "ALLOW"
    AccountReconciler(conn).authorize_order_intent(
        order_intent_id="intent-authorized", account_id="account-1",
        reconciliation_run_id=run.reconciliation_run_id, authorized_at_utc=NOW,
    )
    assert conn.execute(
        """SELECT reconciliation_run_id
           FROM am_order_intent_reconciliation_authorization
           WHERE order_intent_id='intent-authorized'"""
    ).fetchone() == (run.reconciliation_run_id,)
    with pytest.raises(sqlite3.IntegrityError, match="current reconciled account"):
        conn.execute(
            """INSERT INTO am_order_intent VALUES
               ('intent-without-auth','missing-risk','key','PAPER','{}','hash')"""
        )
    replayed = AccountReconciler(conn).reconcile(
        runtime_run_id=runtime_id, account_snapshot_id=snapshot_id,
        account_id="account-1", as_of_utc=NOW, policy=policy(),
        broker_facts=facts(), internal_facts=facts(),
    )
    assert replayed == run
    with pytest.raises(ReconciliationError, match="inputs conflict"):
        AccountReconciler(conn).reconcile(
            runtime_run_id=runtime_id, account_snapshot_id=snapshot_id,
            account_id="account-1", as_of_utc=NOW, policy=policy(),
            broker_facts=facts(holding="999"), internal_facts=facts(),
        )


def test_mismatch_persists_across_next_run_until_explicit_evidenced_resolution():
    conn = database()
    reconciler = AccountReconciler(conn)
    runtime_1, snapshot_1 = snapshot(conn, 1, NOW)
    failed = reconciler.reconcile(
        runtime_run_id=runtime_1, account_snapshot_id=snapshot_1,
        account_id="account-1", as_of_utc=NOW, policy=policy(),
        broker_facts=facts(holding="3"), internal_facts=facts(holding="2"),
    )
    assert failed.status is ReconciliationStatus.BLOCKED
    assert reconciler.trade_gate(
        account_id="account-1", reconciliation_run_id=failed.reconciliation_run_id
    ).action == "NO_NEW_TRADES"
    with pytest.raises(ReconciliationError, match="NO_NEW_TRADES"):
        reconciler.authorize_order_intent(
            order_intent_id="blocked-intent", account_id="account-1",
            reconciliation_run_id=failed.reconciliation_run_id, authorized_at_utc=NOW,
        )
    issue_id = failed.unresolved_issue_ids[0]

    later = NOW + timedelta(seconds=1)
    runtime_2, snapshot_2 = snapshot(conn, 2, later)
    clean = reconciler.reconcile(
        runtime_run_id=runtime_2, account_snapshot_id=snapshot_2,
        account_id="account-1", as_of_utc=later, policy=policy(),
        broker_facts=facts(), internal_facts=facts(),
    )
    assert clean.status is ReconciliationStatus.BLOCKED
    with pytest.raises(ReconciliationError, match="matching reconciliation evidence"):
        reconciler.resolve_issue(
            issue_id=issue_id, evidence_reconciliation_run_id=failed.reconciliation_run_id,
            resolved_at_utc=later, resolution_note="not proven", approved_by="owner",
        )
    for unresolved_id in failed.unresolved_issue_ids:
        reconciler.resolve_issue(
            issue_id=unresolved_id, evidence_reconciliation_run_id=clean.reconciliation_run_id,
            resolved_at_utc=later, resolution_note="broker reload and ledger replay agree",
            approved_by="risk-owner",
        )
    assert conn.execute(
        "SELECT status FROM am_reconciliation_issue_status_v2 WHERE issue_id=?", (issue_id,),
    ).fetchone() == ("RESOLVED",)
    assert reconciler.trade_gate(
        account_id="account-1", reconciliation_run_id=clean.reconciliation_run_id
    ).action == "ALLOW"


def test_missing_or_unapproved_tolerance_is_unverifiable_and_blocks():
    conn = database()
    runtime_id, snapshot_id = snapshot(conn, 1, NOW)
    incomplete_policy = ReconciliationPolicy(
        "incomplete-v1", NOW - timedelta(days=1), None,
        "owner", "test missing tolerance", (),
    )
    run = AccountReconciler(conn).reconcile(
        runtime_run_id=runtime_id, account_snapshot_id=snapshot_id,
        account_id="account-1", as_of_utc=NOW, policy=incomplete_policy,
        broker_facts=facts(), internal_facts=facts(),
    )
    assert run.status is ReconciliationStatus.BLOCKED
    assert any(
        item.status is ReconciliationStatus.UNVERIFIABLE
        and item.action_required == "NO_NEW_TRADES_AND_DEFINE_TOLERANCE"
        for item in run.items
    )


def test_explicit_currency_tolerance_produces_tolerance_match():
    conn = database()
    runtime_id, snapshot_id = snapshot(conn, 1, NOW)
    rules = list(policy().rules)
    rules = [
        rule for rule in rules if rule.target is not ReconciliationTarget.BUYING_POWER
    ]
    rules.append(ToleranceRule(
        ReconciliationTarget.BUYING_POWER, Decimal("0.02"), currency="USD"
    ))
    tolerant = ReconciliationPolicy(
        "currency-tolerance-v1", NOW - timedelta(days=1), None,
        "risk-owner", "USD broker rounding", tuple(rules),
    )
    broker = replace_fact(
        facts(), ReconciliationTarget.BUYING_POWER, "USD", "1000.01"
    )
    run = AccountReconciler(conn).reconcile(
        runtime_run_id=runtime_id, account_snapshot_id=snapshot_id,
        account_id="account-1", as_of_utc=NOW, policy=tolerant,
        broker_facts=broker, internal_facts=facts(),
    )
    item = next(
        item for item in run.items
        if (item.target, item.dimension_key) == (ReconciliationTarget.BUYING_POWER, "USD")
    )
    assert item.status is ReconciliationStatus.TOLERANCE_MATCH
    assert item.difference == Decimal("0.01")
    assert run.status is ReconciliationStatus.TOLERANCE_MATCH


def test_backup_restore_preserves_same_run_items_issues_and_gate():
    source = database()
    runtime_id, snapshot_id = snapshot(source, 1, NOW)
    original = AccountReconciler(source).reconcile(
        runtime_run_id=runtime_id, account_snapshot_id=snapshot_id,
        account_id="account-1", as_of_utc=NOW, policy=policy(),
        broker_facts=facts(holding="3"), internal_facts=facts(),
    )
    source.commit()
    restored = sqlite3.connect(":memory:")
    source.backup(restored)
    rebuilt = AccountReconciler(restored).load_run(original.reconciliation_run_id)
    assert rebuilt == original
    assert AccountReconciler(restored).trade_gate(
        account_id="account-1", reconciliation_run_id=original.reconciliation_run_id
    ) == AccountReconciler(source).trade_gate(
        account_id="account-1", reconciliation_run_id=original.reconciliation_run_id
    )


def test_account_truth_snapshot_is_connected_to_cash_position_and_constraints():
    conn = database()
    runtime_id = "run-connected"
    conn.execute(
        "INSERT INTO am_runtime_run VALUES (?, ?, ?, 'revision', ?)",
        (runtime_id, NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
    )
    raw_id = SQLiteRawResponseStore(conn).append(
        source="toss", endpoint="/api/v1/accounts", http_method="GET",
        request_payload={}, status_code=200, body={"result": []},
        requested_at=NOW, received_at=NOW, account_id="account-1",
        schema_version="1.2.14",
    )
    truth = AccountTruthSnapshot(
        runtime_id, "account-1", NOW,
        ({"accountSeq": "account-1", "cashBalances": {"USD": "1000"}},),
        ({"symbol": "SPY", "currency": "USD", "quantity": "2",
          "averagePurchasePrice": "100", "lastPrice": "100"},),
        (), (), (), ({"currency": "USD", "cashBuyingPower": "1000"},),
        ({"symbol": "SPY", "sellableQuantity": "2"},), (), (), (), (raw_id,),
    )
    snapshot_id = AccountTruthRepository(conn).append(truth)
    CashLedger(conn).record_opening(
        account_id="account-1", currency="USD", as_of_utc=NOW,
        opening_balance="1000", evidence=raw_id, approved_by="owner",
    )
    PositionLedger(conn).record_opening(
        account_id="account-1", instrument_id="SPY", native_currency="USD",
        as_of_utc=NOW, quantity="2", average_cost="100", evidence=raw_id,
        approved_by="owner", tax_policy_version="FIFO-v1",
    )
    run = AccountReconciler(conn).reconcile_snapshot(
        account_snapshot_id=snapshot_id, as_of_utc=NOW, policy=policy()
    )
    assert run.status is ReconciliationStatus.MATCH
    assert AccountReconciler(conn).trade_gate(
        account_id="account-1", reconciliation_run_id=run.reconciliation_run_id
    ).action == "ALLOW"


def test_snapshot_without_broker_cash_truth_is_explicitly_unverifiable():
    conn = database()
    runtime_id, snapshot_id = snapshot(conn, 1, NOW)
    run = AccountReconciler(conn).reconcile_snapshot(
        account_snapshot_id=snapshot_id, as_of_utc=NOW, policy=policy()
    )
    cash_item = next(item for item in run.items if item.target is ReconciliationTarget.CASH)
    assert cash_item.status is ReconciliationStatus.UNVERIFIABLE
    assert run.status is ReconciliationStatus.BLOCKED


def test_snapshot_open_order_state_execution_fee_tax_and_settlement_are_connected():
    conn = database()
    runtime_id = "run-open-order"
    conn.execute(
        "INSERT INTO am_runtime_run VALUES (?, ?, ?, 'revision', ?)",
        (runtime_id, NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
    )
    raw_id = SQLiteRawResponseStore(conn).append(
        source="toss", endpoint="/api/v1/orders/open-1", http_method="GET",
        request_payload={}, status_code=200, body={"result": {}},
        requested_at=NOW, received_at=NOW, account_id="account-1",
        schema_version="1.2.14",
    )
    order = {
        "orderId": "open-1", "status": "PENDING", "symbol": "SPY",
        "side": "BUY", "currency": "USD", "quantity": "1", "price": "100",
        "execution": {"filledQuantity": "0", "filledAmount": "0",
                      "averageFilledPrice": None, "commission": "0", "tax": "0",
                      "settlementDate": None},
    }
    truth = AccountTruthSnapshot(
        runtime_id, "account-1", NOW,
        ({"accountSeq": "account-1", "cashBalances": {"USD": "1000"}},),
        (), (order,), (), (order,), ({"currency": "USD", "cashBuyingPower": "900"},),
        (), (), (), (), (raw_id,),
    )
    snapshot_id = AccountTruthRepository(conn).append(truth)
    CashLedger(conn).record_opening(
        account_id="account-1", currency="USD", as_of_utc=NOW,
        opening_balance="1000", evidence=raw_id, approved_by="owner",
    )
    conn.execute(
        """INSERT INTO am_broker_order VALUES
           ('open-1', ?, 'account-1', 'PENDING', ?, ?)""",
        (runtime_id, '{"symbol":"SPY","side":"BUY","currency":"USD",'
                     '"quantity":"1","price":"100"}', raw_id),
    )
    OrderStateRepository(conn).append_broker_state(
        broker_order_id="open-1", raw_state="PENDING", observed_at_utc=NOW,
        source_response_id=raw_id,
    )
    ExecutionSnapshotRepository(conn).append(
        broker_order_id="open-1", cumulative_quantity="0", cumulative_amount="0",
        average_price=None, observed_at_utc=NOW, source_response_id=raw_id,
    )
    CashLedger(conn).reserve_open_order(
        OpenBuyOrder("open-1", "account-1", "USD", remaining_amount=Decimal("100")),
        source_response_id=raw_id, observed_at_utc=NOW,
    )
    run = AccountReconciler(conn).reconcile_snapshot(
        account_snapshot_id=snapshot_id, as_of_utc=NOW, policy=policy()
    )
    assert run.status is ReconciliationStatus.MATCH
    assert all(item.status is ReconciliationStatus.MATCH for item in run.items)
