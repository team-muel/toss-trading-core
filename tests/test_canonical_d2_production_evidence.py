from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.data.repositories import SQLiteTemporalObservationStore
from asset_management.domain.economics import CurrencyBasis
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.governance import (
    ModelDefinition, ModelRegistry, ModelScope, ModelStatus,
    RuntimeModelRegistryEvidenceRepository,
)
from asset_management.ledger import (
    ProviderAccountingContract, ProviderAccountingContractRepository,
    ProviderAccountingSnapshotRepository,
)
from asset_management.ledger import provider_accounting_snapshot as accounting_module
from asset_management.reference.calendars import SessionRepository
from asset_management.risk import (
    FactorExposure, FactorRiskCalculationRepository, MissingPolicy, SpecificRiskPolicy,
)
from asset_management.time.asof import AsOfContext
from asset_management.time.clock import FrozenClock, ReplayClock
from asset_management.validation import CanonicalD2ProductionEvidenceRepository


ROOT = Path(__file__).parents[1]
D = Decimal


def _schema(conn, now):
    Migrator(conn, FrozenClock(now)).migrate(load_migration_catalog(ROOT / "schemas"))


def _provider_statement(now):
    return {"result": {
        "accountId": "account-1", "observedAt": now.isoformat(), "reportingCurrency": "USD",
        "reportedNav": "175",
        "fxRates": [{"baseCurrency": "USD", "quoteCurrency": "USD", "rate": "1"}],
        "components": [
            {"fieldId": "settled", "kind": "CASH", "amount": "100", "currency": "USD", "fxToReporting": "1"},
            {"fieldId": "unsettled", "kind": "UNSETTLED_CASH", "amount": "10", "currency": "USD", "fxToReporting": "1"},
            {"fieldId": "receivable", "kind": "SETTLEMENT_RECEIVABLE", "amount": "20", "currency": "USD", "fxToReporting": "1"},
            {"fieldId": "payable", "kind": "SETTLEMENT_PAYABLE", "amount": "-5", "currency": "USD", "fxToReporting": "1"},
            {"fieldId": "securities", "kind": "SECURITIES", "amount": "50", "currency": "USD", "fxToReporting": "1"},
        ],
        "cashStates": [{
            "currency": "USD", "fxToReporting": "1", "settledCash": "100", "unsettledCash": "10",
            "settlementReceivable": "20", "settlementPayable": "-5", "settledCashFieldId": "settled",
            "unsettledCashFieldId": "unsettled", "settlementReceivableFieldId": "receivable",
            "settlementPayableFieldId": "payable",
        }],
        "holdings": [{"instrumentId": "SPY", "currency": "USD", "quantity": "1", "marketPrice": "50",
                      "marketValue": "50", "fxToReporting": "1"}],
        "brokerBuyingPower": [{"currency": "USD", "cashBuyingPower": "1000"}],
    }}


def _record_provider_snapshot(conn, now, monkeypatch, tmp_path):
    provider, schema, endpoint, approval_endpoint = (
        "verified-broker-statement", "broker-account-statement@1", "/v1/accounting-snapshots",
        "/openapi/accounting-statement",
    )
    registry_path = tmp_path / "provider-contracts.json"
    registry = {"schema_version": "provider-accounting-contract-registry@1", "contracts": [{
        "provider": provider, "endpoint": endpoint, "schema_version": schema,
        "contract_version": "statement-contract@1", "approval_endpoint": approval_endpoint,
        "approval_schema_version": schema, "statement_schema": "normalized-accounting-statement@1",
        "effective_from_utc": (now - timedelta(days=1)).isoformat(), "effective_to_utc": None,
        "approved_by": "test-governance",
    }]}
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(accounting_module, "_provider_contract_registry_path", lambda: registry_path)
    rules, registry_hash = accounting_module._approved_contracts()
    rule = rules[(provider, endpoint, schema, "statement-contract@1")]
    raw = SQLiteRawResponseStore(conn)
    approval_id = raw.append(
        source=provider, endpoint=approval_endpoint, http_method="GET", request_payload={}, status_code=200,
        body={"provider": provider, "endpoint": endpoint, "schemaVersion": schema,
              "contractVersion": "statement-contract@1", "statementSchema": "normalized-accounting-statement@1",
              "effectiveFromUtc": rule.effective_from_utc.isoformat(), "effectiveToUtc": None,
              "approvedBy": "test-governance", "registryContentHash": registry_hash},
        requested_at=now - timedelta(hours=4), received_at=now - timedelta(hours=4), account_id=None,
        schema_version=schema,
    )
    contract_id = ProviderAccountingContractRepository(conn).record(
        ProviderAccountingContract(provider, endpoint, schema, "statement-contract@1", approval_id))
    source_id = raw.append(
        source=provider, endpoint=endpoint, http_method="GET", request_payload={"account": "account-1"},
        status_code=200, body=_provider_statement(now - timedelta(minutes=1)),
        requested_at=now - timedelta(minutes=1), received_at=now - timedelta(minutes=1), account_id="account-1",
        schema_version=schema,
    )
    return ProviderAccountingSnapshotRepository(conn).materialize(
        runtime_run_id="canonical-run@1", provider_contract_id=contract_id, source_response_id=source_id)


def _record_factor_risk(conn, now):
    conn.execute("INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
                 ("returns-ingestion", "canonical-run@1", "returns", now.isoformat(), now.isoformat()))
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 ("returns-manifest", "returns-ingestion", "silver", "total-return", "memory://returns",
                  "a" * 64, now.isoformat(), now.isoformat(), "returns@1", 6))
    observation_store = SQLiteTemporalObservationStore(conn)
    for offset, values in enumerate(((".01", ".02"), ("-.01", ".00"), (".02", "-.01"))):
        instant = now - timedelta(days=3 - offset)
        for instrument, value in zip(("A", "B"), values):
            observation_store.append(entity_id=instrument, field="risk_return:total", value={
                "return": value, "calendar_id": "XNYS", "currency_basis": "BASE", "total_return": True,
            }, reference_period=instant.date().isoformat(), event_time=instant, scheduled_release_at=None,
                official_release_at=instant, source_timestamp=instant, received_at=instant,
                available_at=instant, ingested_at=instant, revised_at=None, source_timezone="UTC",
                schema_version="risk-return@1", dataset_manifest_id="returns-manifest")
            SessionRepository(conn).record(exchange="XNYS", local_date=instant.date().isoformat(), timezone="UTC",
                session_status="OPEN", regular_open=instant.replace(hour=0), regular_close=instant.replace(hour=1),
                effective_from=instant.replace(hour=0), available_at=instant, source="calendar@1")
    registry = ModelRegistry()
    model = ModelDefinition("FACTOR_RISK", "1", "factor risk", ("total_return", "exposure"),
        ("factor_risk",), (ModelScope.RISK_ESTIMATION,), ("stale input",), now.date() - timedelta(days=2),
        now.date() + timedelta(days=2), "risk-owner")
    registry.register(model)
    for index, status in enumerate((ModelStatus.VALIDATED, ModelStatus.APPROVED, ModelStatus.ACTIVE)):
        registry.transition(model.key, status, effective_at=now - timedelta(minutes=4 - index),
                            reason="review", evidence_ids=(f"risk-review-{index}",))
    clock = ReplayClock(now - timedelta(minutes=5))
    model_evidence = RuntimeModelRegistryEvidenceRepository(conn, clock)
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            model_evidence.record_review_evidence(evidence_id, model_key=transition.model_key,
                from_status=transition.from_status, to_status=transition.to_status, owner="risk-owner",
                evidence={"review": "approved"})
    clock.advance_to(now - timedelta(seconds=1))
    snapshot = model_evidence.publish_snapshot(registry)
    clock.advance_to(now)
    model_evidence.bind_runtime_run("canonical-run@1", snapshot)
    authorization = model_evidence.authorize("canonical-run@1", model_key="FACTOR_RISK@1",
                                              scope=ModelScope.RISK_ESTIMATION)
    factor = FactorRiskCalculationRepository(conn, FrozenClock(now))
    context = AsOfContext("canonical-run@1", now, now, "policy@1", "parameters@1", "git:canonical")
    estimator = factor.record_estimator_evidence(
        context=context, model_key="FACTOR_RISK@1", model_registry_evidence=model_evidence,
        runtime_authorization=authorization,
        exposures=(FactorExposure("A", (D(1),), now, now, "exposure@1"),
                   FactorExposure("B", (D(".5"),), now, now, "exposure@1")),
        factor_matrix=((D(".04"),),), residual_variance=(D(".02"), D(".03")), residual_history=(30, 30),
        residual_serial_correlation=(D(".1"), D(".2")), residual_heteroskedasticity=(D(".1"), D(".2")),
        policy=SpecificRiskPolicy(20, D(".01"), D(".25"), D(".10"), "specific-risk@1"),
        maximum_exposure_age_days=0, maximum_return_age_days=3)
    return factor.calculate(context=context, instruments=("A", "B"), return_field="risk_return:total",
        calendar_id="XNYS", currency_basis=CurrencyBasis.BASE, missing_policy=MissingPolicy.FAIL,
        estimator_evidence_id=estimator, model_key="FACTOR_RISK@1",
        model_registry_evidence=model_evidence, runtime_authorization=authorization)


def _fred_manifest(conn, now, tmp_path):
    store = ImmutableDatasetStore(tmp_path / "immutable")
    body = {"observations": [{"series_id": series, "as_of": now.isoformat(),
                                "available_at": now.isoformat(), "value_percent": "4.00"}
                               for series in ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1")]}
    manifest = store.write(body, layer="bronze", source="fred-alfred", dataset="risk-free-curve",
        schema_version="fred-risk-free@1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag="purpose=research;redistribution=forbidden;retention=project", code_revision="git:canonical",
        request_hash="b" * 64, quality_status="RAW")
    conn.execute("INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
                 ("fred-ingestion", "canonical-run@1", "fred-alfred", now.isoformat(), now.isoformat()))
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (manifest.manifest_id, "fred-ingestion", "bronze", "risk-free-curve", "memory://fred",
                  manifest.content_sha256, now.isoformat(), now.isoformat(), "fred-risk-free@1", 4))
    return store, manifest.manifest_id


def test_canonical_evidence_replays_only_a_complete_persisted_bundle(monkeypatch, tmp_path):
    # The test constructs temporary persisted records; the production entry point
    # accepts none of these economic values from its caller.
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", now.isoformat()))
    factor = _record_factor_risk(conn, now)
    accounting = _record_provider_snapshot(conn, now, monkeypatch, tmp_path)
    store, manifest_id = _fred_manifest(conn, now, tmp_path)
    repository = CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now))
    recorded = repository.record(runtime_run_id="canonical-run@1", risk_free_manifest_id=manifest_id, store=store)
    assert recorded.factor_risk_calculation_id == factor.factor_risk_calculation_id
    assert recorded.accounting_snapshot_id == accounting.accounting_snapshot_id
    assert repository.replay(runtime_run_id="canonical-run@1", store=store) == recorded
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE am_canonical_d2_production_evidence SET content_hash='0'")


def test_canonical_evidence_does_not_create_a_run_when_persisted_evidence_is_absent(tmp_path):
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("empty-run", now.isoformat(), now.isoformat(), "git:empty", now.isoformat()))
    repository = CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now))
    with pytest.raises(DataQualityError, match="CANONICAL_D2_MODEL_REGISTRY_MISSING"):
        repository.record(runtime_run_id="empty-run", risk_free_manifest_id="a" * 64,
                          store=ImmutableDatasetStore(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM am_canonical_d2_production_evidence").fetchone()[0] == 0
