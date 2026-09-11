from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path

import pytest

from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import ReconciliationError
from asset_management.ledger import (
    ProviderAccountingContract, ProviderAccountingContractRepository,
    ProviderAccountingSnapshotRepository,
)
from asset_management.ledger import provider_accounting_snapshot as accounting_snapshot_module


ROOT = Path(__file__).parents[1]
# Contract recording time is read from SQLite's UTC clock.  Keep source
# evidence deliberately after that clock and the runtime cut-off after source
# receipt, rather than injecting a caller-controlled contract timestamp.
NOW = datetime.now(timezone.utc).replace(microsecond=0)
STATEMENT_TIME = NOW + timedelta(hours=1)
RUNTIME_TIME = NOW + timedelta(hours=2)
PROVIDER = "verified-broker-statement"
SCHEMA = "broker-account-statement@1"
ENDPOINT = "/v1/accounting-snapshots"
APPROVAL_ENDPOINT = "/openapi/accounting-statement"


def schema(conn):
    conn.executescript((ROOT / "schemas/asset_management.sql").read_text(encoding="utf-8"))
    for path in sorted((ROOT / "schemas/migrations").glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    value = sqlite3.connect(":memory:")
    value.execute("PRAGMA foreign_keys=ON")
    schema(value)
    value.execute("INSERT INTO am_runtime_run VALUES ('run-1', ?, ?, 'revision', ?)",
                  (RUNTIME_TIME.isoformat(), RUNTIME_TIME.isoformat(), RUNTIME_TIME.isoformat()))
    yield value
    value.close()


@pytest.fixture(autouse=True)
def approved_registry(monkeypatch, tmp_path):
    """Test-only registry fixture; production's committed registry is empty."""
    path = tmp_path / "provider_accounting_contracts.json"
    path.write_text(json.dumps({
        "schema_version": "provider-accounting-contract-registry@1",
        "contracts": [{
            "provider": PROVIDER, "endpoint": ENDPOINT, "schema_version": SCHEMA,
            "contract_version": "statement-contract@1", "approval_endpoint": APPROVAL_ENDPOINT,
            "approval_schema_version": SCHEMA, "statement_schema": "normalized-accounting-statement@1",
            "effective_from_utc": (NOW - timedelta(days=1)).isoformat(), "effective_to_utc": None,
            "approved_by": "test-governance",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(accounting_snapshot_module, "_provider_contract_registry_path", lambda: path)


def approval_artifact():
    rules, registry_hash = accounting_snapshot_module._approved_contracts()
    rule = rules[(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1")]
    return {
        "provider": rule.provider,
        "endpoint": rule.endpoint,
        "schemaVersion": rule.schema_version,
        "contractVersion": rule.contract_version,
        "statementSchema": "normalized-accounting-statement@1",
        "effectiveFromUtc": rule.effective_from_utc.isoformat(),
        "effectiveToUtc": rule.effective_to_utc.isoformat() if rule.effective_to_utc else None,
        "approvedBy": rule.approved_by,
        "registryContentHash": registry_hash,
    }


def append_approval(conn, *, body=None, requested_at=NOW, received_at=NOW):
    return append_raw(conn, endpoint=APPROVAL_ENDPOINT,
                      body=approval_artifact() if body is None else body, account_id=None,
                      requested_at=requested_at, received_at=received_at)


def append_raw(conn, *, endpoint=ENDPOINT, body=None, account_id="account-1",
               received_at=STATEMENT_TIME, requested_at=STATEMENT_TIME,
               source=PROVIDER, schema_version=SCHEMA):
    if body is None:
        body = statement()
    return SQLiteRawResponseStore(conn).append(
        source=source, endpoint=endpoint, http_method="GET", request_payload={"account": account_id},
        status_code=200, body=body, requested_at=requested_at, received_at=received_at,
        account_id=account_id, schema_version=schema_version,
    )


def statement(**changes):
    result = {
        "accountId": "account-1", "observedAt": NOW.isoformat(), "reportingCurrency": "USD",
        "reportedNav": "175",
        "fxRates": [{"baseCurrency": "USD", "quoteCurrency": "USD", "rate": "1"}],
        "components": [
            {"fieldId": "settled-usd", "kind": "CASH", "amount": "100", "currency": "USD", "fxToReporting": "1"},
            {"fieldId": "unsettled-usd", "kind": "UNSETTLED_CASH", "amount": "10", "currency": "USD", "fxToReporting": "1"},
            {"fieldId": "receivable-usd", "kind": "SETTLEMENT_RECEIVABLE", "amount": "20", "currency": "USD", "fxToReporting": "1"},
            {"fieldId": "payable-usd", "kind": "SETTLEMENT_PAYABLE", "amount": "-5", "currency": "USD", "fxToReporting": "1"},
            {"fieldId": "securities-usd", "kind": "SECURITIES", "amount": "50", "currency": "USD", "fxToReporting": "1"},
        ],
        "cashStates": [{
            "currency": "USD", "fxToReporting": "1", "settledCash": "100",
            "unsettledCash": "10", "settlementReceivable": "20", "settlementPayable": "-5",
            "settledCashFieldId": "settled-usd", "unsettledCashFieldId": "unsettled-usd",
            "settlementReceivableFieldId": "receivable-usd", "settlementPayableFieldId": "payable-usd",
        }],
        "holdings": [{"instrumentId": "SPY", "currency": "USD", "quantity": "1",
                      "marketPrice": "50", "marketValue": "50", "fxToReporting": "1"}],
        # This is evidence of an external order constraint, not a cash asset or NAV component.
        "brokerBuyingPower": [{"currency": "USD", "cashBuyingPower": "1000"}],
    }
    result.update(changes)
    return {"result": result}


def contract(conn):
    approval = append_approval(conn)
    item = ProviderAccountingContract(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", approval)
    identifier = ProviderAccountingContractRepository(conn).record(item)
    return identifier


def materialize(conn, **changes):
    args = dict(runtime_run_id="run-1", provider_contract_id=contract(conn),
                source_response_id=append_raw(conn))
    args.update(changes)
    return ProviderAccountingSnapshotRepository(conn).materialize(**args)


def test_complete_provider_statement_is_persisted_and_replays_exactly(conn):
    snapshot = materialize(conn)
    assert snapshot.runtime_run_id == "run-1"
    assert snapshot.payload["reported_nav"] == "175"
    assert snapshot.payload["cash_states"] == [{
        "currency": "USD", "fx_to_reporting": "1", "settled_cash": "100",
        "settled_cash_field_id": "settled-usd", "unsettled_cash": "10",
        "unsettled_cash_field_id": "unsettled-usd", "settlement_receivable": "20",
        "settlement_receivable_field_id": "receivable-usd", "settlement_payable": "-5",
        "settlement_payable_field_id": "payable-usd",
    }]
    assert snapshot.payload["holdings"][0]["market_value"] == "50"
    assert snapshot.payload["fx_rates"] == [{"base_currency": "USD", "reporting_currency": "USD", "rate": "1"}]
    assert snapshot.payload["broker_buying_power"] == [{
        "currency": "USD", "cash_buying_power": "1000", "source_path": "brokerBuyingPower[0]"
    }]
    assert all(item["kind"] != "BROKER_BUYING_POWER" for item in snapshot.payload["nav_basis"]["components"])
    assert snapshot.payload["nav_basis"]["value"] == "175"
    assert ProviderAccountingSnapshotRepository(conn).replay(snapshot.accounting_snapshot_id) == snapshot
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE am_provider_accounting_snapshot SET account_id='different'")


def test_materialization_has_no_caller_supplied_economic_values(conn):
    with pytest.raises(TypeError):
        ProviderAccountingSnapshotRepository(conn).materialize(
            runtime_run_id="run-1", provider_contract_id=contract(conn),
            source_response_id=append_raw(conn), reported_nav="999999",
        )
    snapshot = materialize(conn)
    assert snapshot.payload["reported_nav"] == "175"
    assert snapshot.payload["broker_buying_power"][0]["cash_buying_power"] == "1000"


@pytest.mark.parametrize("change", [
    {"cashStates": []},
    {"brokerBuyingPower": []},
    {"holdings": []},
    {"reportedNav": "174"},
    {"reportingCurrency": "USDX"},
])
def test_incomplete_or_mismatched_provider_statement_fails_closed(conn, change):
    with pytest.raises(ReconciliationError):
        materialize(conn, source_response_id=append_raw(conn, body=statement(**change)))


def test_cash_state_holding_and_fx_must_reconcile_to_provider_components(conn):
    bad_cash = statement()
    bad_cash["result"]["cashStates"][0]["settledCash"] = "101"
    with pytest.raises(ReconciliationError, match="CASH_STATE_COMPONENT_MISMATCH"):
        materialize(conn, source_response_id=append_raw(conn, body=bad_cash))

    bad_holding = statement()
    bad_holding["result"]["holdings"][0]["marketValue"] = "51"
    with pytest.raises(ReconciliationError, match="HOLDING_INVALID"):
        materialize(conn, source_response_id=append_raw(conn, body=bad_holding))

    bad_fx = statement()
    bad_fx["result"]["components"][0]["fxToReporting"] = "2"
    with pytest.raises(ReconciliationError, match="COMPONENT_FX_INVALID"):
        materialize(conn, source_response_id=append_raw(conn, body=bad_fx))


def test_unverified_wrong_source_and_time_ineligible_evidence_cannot_materialize(conn):
    identifier = contract(conn)
    with pytest.raises(ReconciliationError, match="RAW_INVALID"):
        ProviderAccountingSnapshotRepository(conn).materialize(
            runtime_run_id="run-1", provider_contract_id=identifier, source_response_id="missing")
    with pytest.raises(ReconciliationError, match="SOURCE_CONTEXT_INVALID"):
        ProviderAccountingSnapshotRepository(conn).materialize(
            runtime_run_id="run-1", provider_contract_id=identifier,
            source_response_id=append_raw(conn, source="toss"))
    with pytest.raises(ReconciliationError, match="TIME_INVALID"):
        materialize(conn, source_response_id=append_raw(conn, received_at=RUNTIME_TIME + timedelta(seconds=1),
                                                         requested_at=RUNTIME_TIME + timedelta(seconds=1)))


def test_toss_buying_power_or_holdings_cannot_be_relabelled_as_a_complete_accounting_statement(conn):
    approval = append_raw(conn, source="toss", endpoint="/api/v1/accounts", body={"result": []}, account_id=None,
                          schema_version="1.2.15")
    with pytest.raises(ReconciliationError, match="CONTRACT_UNSUPPORTED"):
        ProviderAccountingContract("TOSS", "/api/v1/holdings", "1.2.15", "toss-holdings@1", approval)


def test_buying_power_is_not_an_accounting_nav_component(conn):
    body = statement()
    body["result"]["components"].append({
        "fieldId": "forbidden-power", "kind": "BROKER_BUYING_POWER", "amount": "1000",
        "currency": "USD", "fxToReporting": "1",
    })
    with pytest.raises(ReconciliationError, match="COMPONENT_INVALID"):
        materialize(conn, source_response_id=append_raw(conn, body=body))


def test_contract_requires_immutable_provider_approval_and_cannot_be_changed(conn):
    item = ProviderAccountingContract(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", "not-persisted")
    with pytest.raises(ReconciliationError, match="CONTRACT_APPROVAL_INVALID"):
        ProviderAccountingContractRepository(conn).record(item)
    identifier = contract(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE am_provider_accounting_contract SET endpoint='/other' WHERE provider_contract_id=?", (identifier,))


def test_production_registry_has_no_unapproved_provider_path(conn, monkeypatch):
    monkeypatch.undo()
    approval = append_raw(conn, endpoint=APPROVAL_ENDPOINT, body={"openapi": "approved"}, account_id=None,
                          requested_at=NOW, received_at=NOW)
    item = ProviderAccountingContract(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", approval)
    with pytest.raises(ReconciliationError, match="CONTRACT_UNAPPROVED"):
        ProviderAccountingContractRepository(conn).record(item)


def test_approval_endpoint_and_historical_contract_ordering_are_enforced(conn):
    wrong_approval = append_raw(conn, endpoint="/unreviewed-contract", body=approval_artifact(),
                                account_id=None, requested_at=NOW, received_at=NOW)
    item = ProviderAccountingContract(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", wrong_approval)
    with pytest.raises(ReconciliationError, match="CONTRACT_APPROVAL_INVALID"):
        ProviderAccountingContractRepository(conn).record(item)

    identifier = contract(conn)
    past = NOW - timedelta(minutes=1)
    historic = statement()
    historic["result"]["observedAt"] = past.isoformat()
    with pytest.raises(ReconciliationError, match="CONTRACT_NOT_EFFECTIVE"):
        ProviderAccountingSnapshotRepository(conn).materialize(
            runtime_run_id="run-1", provider_contract_id=identifier,
            source_response_id=append_raw(conn, body=historic, requested_at=past, received_at=past),
        )


def test_registry_cannot_be_overridden_from_the_current_working_directory(conn, monkeypatch, tmp_path):
    monkeypatch.undo()
    override = tmp_path / "config"
    override.mkdir()
    (override / "provider_accounting_contracts.json").write_text(json.dumps({
        "schema_version": "provider-accounting-contract-registry@1", "contracts": [{
            "provider": PROVIDER, "endpoint": ENDPOINT, "schema_version": SCHEMA,
            "contract_version": "statement-contract@1", "approval_endpoint": APPROVAL_ENDPOINT,
            "approval_schema_version": SCHEMA, "statement_schema": "normalized-accounting-statement@1",
            "effective_from_utc": (NOW - timedelta(days=1)).isoformat(), "effective_to_utc": None,
            "approved_by": "untrusted-cwd",
        }],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    approval = append_raw(conn, endpoint=APPROVAL_ENDPOINT, body={"openapi": "approved"}, account_id=None,
                          requested_at=NOW, received_at=NOW)
    item = ProviderAccountingContract(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", approval)
    with pytest.raises(ReconciliationError, match="CONTRACT_UNAPPROVED"):
        ProviderAccountingContractRepository(conn).record(item)


def test_contract_recording_time_is_store_owned_and_cannot_be_backdated(conn):
    historical = NOW - timedelta(minutes=1)
    approval = append_approval(conn, requested_at=historical, received_at=historical)
    source = append_raw(conn, body=statement(observedAt=historical.isoformat()),
                        requested_at=historical, received_at=historical)
    item = ProviderAccountingContract(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", approval)
    repository = ProviderAccountingContractRepository(conn)
    with pytest.raises(TypeError):
        repository.record(item, recorded_at_utc=historical)
    identifier = repository.record(item)
    with pytest.raises(ReconciliationError, match="CONTRACT_NOT_EFFECTIVE"):
        ProviderAccountingSnapshotRepository(conn).materialize(
            runtime_run_id="run-1", provider_contract_id=identifier, source_response_id=source,
        )


def test_existing_contract_recovery_does_not_depend_on_a_rotated_registry(conn, monkeypatch, tmp_path):
    approval = append_approval(conn)
    item = ProviderAccountingContract(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", approval)
    repository = ProviderAccountingContractRepository(conn)
    identifier = repository.record(item)
    rotated = tmp_path / "rotated-registry.json"
    rotated.write_text(json.dumps({"schema_version": "provider-accounting-contract-registry@1", "contracts": []}),
                       encoding="utf-8")
    monkeypatch.setattr(accounting_snapshot_module, "_provider_contract_registry_path", lambda: rotated)
    assert repository.record(item) == identifier


def test_mismatched_contract_payload_and_column_approval_ids_fail_closed(conn):
    approval_a = append_approval(conn)
    approval_b = append_approval(conn)
    item_b = ProviderAccountingContract(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", approval_b)
    rules, registry_hash = accounting_snapshot_module._approved_contracts()
    rule = rules[(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1")]
    payload = item_b.payload() | {"approved_rule": accounting_snapshot_module._rule_payload(rule, registry_hash)}
    content_hash = digest(canonical(payload))
    identifier = f"provider-accounting-contract:{content_hash}"
    conn.execute(
        """INSERT INTO am_provider_accounting_contract
           (provider_contract_id, provider, endpoint, http_method, provider_schema_version,
            approval_evidence_id, payload_json, content_hash, recorded_at_utc)
           VALUES (?, ?, ?, 'GET', ?, ?, ?, ?, ?)""",
        (identifier, PROVIDER, ENDPOINT, SCHEMA, approval_a,
         json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), content_hash,
         NOW.isoformat()),
    )
    conn.commit()
    with pytest.raises(ReconciliationError, match="CONTRACT_ID_CONFLICT"):
        ProviderAccountingContractRepository(conn).record(item_b)
    with pytest.raises(ReconciliationError, match="CONTRACT_INVALID"):
        ProviderAccountingSnapshotRepository(conn).materialize(
            runtime_run_id="run-1", provider_contract_id=identifier, source_response_id=append_raw(conn),
        )


def test_forged_self_consistent_contract_row_requires_matching_approval_artifact(conn):
    approval = append_approval(conn)
    item = ProviderAccountingContract(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", approval)
    rules, registry_hash = accounting_snapshot_module._approved_contracts()
    rule = rules[(PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1")]
    forged_rule = accounting_snapshot_module._rule_payload(rule, registry_hash)
    forged_rule["approved_by"] = "forged-governance"
    payload = item.payload() | {"approved_rule": forged_rule}
    content_hash = digest(canonical(payload))
    identifier = f"provider-accounting-contract:{content_hash}"
    conn.execute(
        """INSERT INTO am_provider_accounting_contract
           (provider_contract_id, provider, endpoint, http_method, provider_schema_version,
            approval_evidence_id, payload_json, content_hash, recorded_at_utc)
           VALUES (?, ?, ?, 'GET', ?, ?, ?, ?, ?)""",
        (identifier, PROVIDER, ENDPOINT, SCHEMA, approval,
         json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), content_hash,
         NOW.isoformat()),
    )
    conn.commit()
    with pytest.raises(ReconciliationError, match="CONTRACT_INVALID"):
        ProviderAccountingSnapshotRepository(conn).materialize(
            runtime_run_id="run-1", provider_contract_id=identifier, source_response_id=append_raw(conn),
        )


def test_approval_artifact_must_bind_the_current_registry_digest(conn):
    forged_approval = approval_artifact()
    forged_approval["registryContentHash"] = "0" * 64
    item = ProviderAccountingContract(
        PROVIDER, ENDPOINT, SCHEMA, "statement-contract@1", append_approval(conn, body=forged_approval),
    )
    with pytest.raises(ReconciliationError, match="CONTRACT_APPROVAL_INVALID"):
        ProviderAccountingContractRepository(conn).record(item)


def test_cash_and_holdings_component_coverage_is_exhaustive(conn):
    extra_cash = statement()
    extra_cash["result"]["components"].append({
        "fieldId": "unmapped-cash", "kind": "CASH", "amount": "1", "currency": "USD", "fxToReporting": "1",
    })
    extra_cash["result"]["reportedNav"] = "176"
    with pytest.raises(ReconciliationError, match="CASH_STATE_COVERAGE_INCOMPLETE"):
        materialize(conn, source_response_id=append_raw(conn, body=extra_cash))

    hidden_security = statement()
    hidden_security["result"]["components"].append({
        "fieldId": "hidden-security", "kind": "SECURITIES", "amount": "1", "currency": "USD",
        "fxToReporting": "1", "includedInField": "securities-usd",
    })
    with pytest.raises(ReconciliationError, match="COMPONENT_INVALID"):
        materialize(conn, source_response_id=append_raw(conn, body=hidden_security))

    hidden_cash = statement()
    hidden_cash["result"]["components"][0]["includedInField"] = "securities-usd"
    hidden_cash["result"]["reportedNav"] = "75"
    with pytest.raises(ReconciliationError, match="COMPONENT_INVALID"):
        materialize(conn, source_response_id=append_raw(conn, body=hidden_cash))


def test_persisted_approval_rule_replays_when_current_registry_changes(conn, monkeypatch, tmp_path):
    snapshot = materialize(conn)
    empty = tmp_path / "empty-registry.json"
    empty.write_text(json.dumps({"schema_version": "provider-accounting-contract-registry@1", "contracts": []}),
                     encoding="utf-8")
    monkeypatch.setattr(accounting_snapshot_module, "_provider_contract_registry_path", lambda: empty)
    assert ProviderAccountingSnapshotRepository(conn).replay(snapshot.accounting_snapshot_id) == snapshot
