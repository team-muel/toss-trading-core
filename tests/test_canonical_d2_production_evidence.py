from base64 import b64encode
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
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
from asset_management.validation import external_attestation as attestation_module
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


ROOT = Path(__file__).parents[1]
D = Decimal


def _attestor(monkeypatch, conn, bound_at):
    authority_id = "test-registry-governance"
    authority_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(attestation_module, "_TRUSTED_REGISTRY_AUTHORITIES", {
        authority_id: authority_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
    })
    private_key = Ed25519PrivateKey.generate()
    payload = {"schema_version": "canonical-evidence-attestor-registry@1", "attestors": [{
        "attestor_id": "test-attestor", "algorithm": "ed25519",
        "public_key_base64": b64encode(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode(),
        "effective_from_utc": "2020-01-01T00:00:00+00:00", "effective_to_utc": None,
    }]}
    snapshot_id = digest(canonical(payload))
    authorization = attestation_module.registry_authorization_payload(
        authority_id=authority_id, snapshot_id=snapshot_id, registry_hash=snapshot_id,
        published_at=bound_at)
    authority_signature = b64encode(authority_key.sign(canonical(authorization))).decode()
    authorization_hash = digest(canonical({"payload": authorization,
                                           "signature_base64": authority_signature}))
    snapshot_hash = digest(canonical({"registry": payload, "published_at": bound_at.isoformat(),
                                      "registry_authorization_hash": authorization_hash}))
    conn.execute("INSERT INTO am_evidence_attestor_registry_snapshot VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (snapshot_id, json.dumps(payload, sort_keys=True, separators=(",", ":")), snapshot_hash,
                  bound_at.isoformat(), authority_id,
                  json.dumps(authorization, sort_keys=True, separators=(",", ":")), authority_signature))
    runtime = conn.execute(
        "SELECT as_of_utc, information_cutoff_utc, code_revision, created_at_utc "
        "FROM am_runtime_run WHERE runtime_run_id='canonical-run@1'").fetchone()
    binding = {"runtime_run_id": "canonical-run@1",
               "evidence_attestor_registry_snapshot_id": snapshot_id,
               "snapshot_content_hash": snapshot_hash,
               "runtime": {"as_of": str(runtime[0]), "information_cutoff": str(runtime[1]),
                           "code_revision": str(runtime[2]), "created_at": str(runtime[3])},
               "bound_at": bound_at.isoformat()}
    binding_hash = digest(canonical(binding))
    conn.execute("INSERT INTO am_runtime_evidence_attestor_registry VALUES (?, ?, ?, ?)",
                 ("canonical-run@1", snapshot_id, bound_at.isoformat(), binding_hash))
    return attestation_module.RuntimeAttestorRegistry(
        snapshot_id, snapshot_hash, bound_at,
        {"test-attestor": (private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
                            datetime(2020, 1, 1, tzinfo=timezone.utc), None)}), "test-attestor", private_key


def _attest(conn, raw_id, attestor, issued_at):
    registry, attestor_id, private_key = attestor
    response = SQLiteRawResponseStore(conn).verified(raw_id)
    payload = attestation_module.attestation_payload(raw_response_id=raw_id, response=response,
                                                     attestor_id=attestor_id, issued_at=issued_at,
                                                     registry=registry)
    signature = b64encode(private_key.sign(canonical(payload))).decode()
    content_hash = digest(canonical({"payload": payload, "signature_base64": signature}))
    conn.execute("INSERT INTO am_external_evidence_attestation VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (content_hash, raw_id, attestor_id, json.dumps(payload, sort_keys=True, separators=(",", ":")),
                  signature, content_hash, issued_at.isoformat()))


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


def _record_provider_snapshot(conn, now, monkeypatch, tmp_path, attestor, *, attest_raw=True):
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
    if attest_raw:
        _attest(conn, approval_id, attestor, now - timedelta(hours=4))
    contract_id = ProviderAccountingContractRepository(conn).record(
        ProviderAccountingContract(provider, endpoint, schema, "statement-contract@1", approval_id))
    source_id = raw.append(
        source=provider, endpoint=endpoint, http_method="GET", request_payload={"account": "account-1"},
        status_code=200, body=_provider_statement(now - timedelta(minutes=1)),
        requested_at=now - timedelta(minutes=1), received_at=now - timedelta(minutes=1), account_id="account-1",
        schema_version=schema,
    )
    if attest_raw:
        _attest(conn, source_id, attestor, now - timedelta(minutes=1))
    return ProviderAccountingSnapshotRepository(conn).materialize(
        runtime_run_id="canonical-run@1", provider_contract_id=contract_id, source_response_id=source_id)


def _record_factor_risk(conn, now, attestor, *, external_reviews=True):
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
    raw = SQLiteRawResponseStore(conn)
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            reviewed_at = clock.now_utc()
            raw_body = {"evidence_id": evidence_id, "model_key": transition.model_key,
                        "from_status": transition.from_status.value, "to_status": transition.to_status.value,
                        "owner": "risk-owner", "decision": "APPROVED", "reviewed_at": reviewed_at.isoformat(),
                        "model_registry_snapshot_id": registry.registry_hash}
            raw_id = raw.append(source="model-governance", endpoint="/v1/model-governance/reviews",
                http_method="GET", request_payload={"evidence_id": evidence_id}, status_code=200,
                body=raw_body, requested_at=reviewed_at, received_at=reviewed_at, account_id=None,
                schema_version="model-governance-review@1")
            _attest(conn, raw_id, attestor, reviewed_at)
            review_id = model_evidence.record_review_evidence(evidence_id, model_key=transition.model_key,
                from_status=transition.from_status, to_status=transition.to_status, owner="risk-owner",
                evidence={"source_response_id": raw_id, "decision": "APPROVED",
                          "reviewed_at": reviewed_at.isoformat()})
            if external_reviews:
                response_hash = raw.verified(raw_id).response_hash
                provenance = {"schema_version": "model-governance-review-raw-provenance@1",
                              "review_evidence_id": review_id, "raw_response_id": raw_id,
                              "raw_response_hash": response_hash}
                conn.execute("INSERT INTO am_model_governance_review_raw_provenance VALUES (?, ?, ?, ?)",
                             (review_id, raw_id, digest(canonical(provenance)), reviewed_at.isoformat()))
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
    persisted = factor.calculate(context=context, instruments=("A", "B"), return_field="risk_return:total",
        calendar_id="XNYS", currency_basis=CurrencyBasis.BASE, missing_policy=MissingPolicy.FAIL,
        estimator_evidence_id=estimator, model_key="FACTOR_RISK@1",
        model_registry_evidence=model_evidence, runtime_authorization=authorization)
    return persisted, estimator


def _estimator_provenance(conn, now, estimator_id, store, attestor):
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM am_factor_risk_estimator_evidence WHERE factor_risk_estimator_evidence_id=?",
        (estimator_id,)).fetchone()[0])
    body = {"estimator": payload}
    raw = SQLiteRawResponseStore(conn)
    raw_id = raw.append(source="factor-risk-provider", endpoint="/v1/factor-risk/estimator-inputs",
        http_method="GET", request_payload={"runtime_run_id": "canonical-run@1"}, status_code=200,
        body=body, requested_at=now, received_at=now, account_id=None,
        schema_version="factor-risk-estimator-inputs@1")
    _attest(conn, raw_id, attestor, now)
    manifest = store.write(body, layer="bronze", source="factor-risk-provider",
        dataset="factor-risk-estimator-inputs", schema_version="factor-risk-estimator-inputs@1",
        retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag="purpose=research;redistribution=forbidden;retention=project", code_revision="git:canonical",
        request_hash="c" * 64, quality_status="RAW")
    conn.execute("INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
                 ("estimator-ingestion", "canonical-run@1", "factor-risk-provider", now.isoformat(), now.isoformat()))
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (manifest.manifest_id, "estimator-ingestion", "bronze", "factor-risk-estimator-inputs",
                  "memory://estimator", manifest.content_sha256, now.isoformat(), now.isoformat(),
                  "factor-risk-estimator-inputs@1", 1))
    provenance = {"schema_version": "factor-risk-estimator-raw-provenance@1",
                  "factor_risk_estimator_evidence_id": estimator_id, "source_manifest_id": manifest.manifest_id,
                  "raw_response_id": raw_id, "raw_response_hash": raw.verified(raw_id).response_hash}
    conn.execute("INSERT INTO am_factor_risk_estimator_raw_provenance VALUES (?, ?, ?, ?, ?)",
                 (estimator_id, manifest.manifest_id, raw_id, digest(canonical(provenance)), now.isoformat()))


def _fred_manifest(conn, now, store, attestor, *, observation_at=None, attest_raw=True,
                   attestation_issued_at=None):
    observation_at = observation_at or now
    body = {"observations": [{"series_id": series, "as_of": observation_at.isoformat(),
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
    policy = {"schema_version": "fred-risk-free-freshness-policy@1",
              "fred_risk_free_freshness_policy_id": "fred-usd-risk-free-freshness@1",
              "maximum_observation_age_seconds": 259200}
    policy_hash = digest(canonical(policy))
    conn.execute("INSERT INTO am_fred_risk_free_freshness_policy VALUES (?, ?, ?, ?)",
                 (policy["fred_risk_free_freshness_policy_id"], policy["maximum_observation_age_seconds"],
                  policy_hash, now.isoformat()))
    raw_store = SQLiteRawResponseStore(conn)
    raw_rows = []
    for series in ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1"):
        raw_id = raw_store.append(source="fred-alfred", endpoint=(
            f"https://api.stlouisfed.org/fred/series/observations?series_id={series}&output_type=3"),
            http_method="GET", request_payload={"series_id": series, "output_type": 3}, status_code=200,
            body={"observations": [{"date": observation_at.date().isoformat(), "value": "4.00"}]},
            requested_at=now, received_at=now, account_id=None,
            schema_version="fred-observations-output-type-3-v1")
        if attest_raw:
            _attest(conn, raw_id, attestor,
                    now if attestation_issued_at is None else attestation_issued_at)
        conn.execute("INSERT INTO am_runtime_fred_risk_free_raw_provenance VALUES (?, ?, ?)",
                     ("canonical-run@1", series, raw_id))
        raw_rows.append({"series_id": series, "raw_response_id": raw_id,
                         "response_hash": raw_store.verified(raw_id).response_hash})
    artifact = {"schema_version": "runtime-fred-risk-free-artifact@1", "runtime_run_id": "canonical-run@1",
                "risk_free_manifest_id": manifest.manifest_id,
                "fred_risk_free_freshness_policy_id": policy["fred_risk_free_freshness_policy_id"],
                "raw_responses": sorted(raw_rows, key=lambda item: item["series_id"])}
    conn.execute("INSERT INTO am_runtime_fred_risk_free_artifact VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", manifest.manifest_id, policy["fred_risk_free_freshness_policy_id"],
                  digest(canonical(artifact)), now.isoformat()))
    return manifest.manifest_id


def test_canonical_evidence_replays_only_a_complete_persisted_bundle(monkeypatch, tmp_path):
    # The test constructs temporary persisted records; the production entry point
    # accepts none of these economic values from its caller.
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", (now - timedelta(hours=5)).isoformat()))
    attestor = _attestor(monkeypatch, conn, now - timedelta(hours=5))
    factor, estimator = _record_factor_risk(conn, now, attestor)
    accounting = _record_provider_snapshot(conn, now, monkeypatch, tmp_path, attestor)
    store = ImmutableDatasetStore(tmp_path / "immutable")
    _estimator_provenance(conn, now, estimator, store, attestor)
    manifest_id = _fred_manifest(conn, now, store, attestor)
    repository = CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now))
    recorded = repository.record(runtime_run_id="canonical-run@1", store=store)
    assert recorded.factor_risk_calculation_id == factor.factor_risk_calculation_id
    assert recorded.accounting_snapshot_id == accounting.accounting_snapshot_id
    assert "external_attestor_registry" in json.loads(conn.execute(
        "SELECT payload_json FROM am_canonical_d2_production_evidence").fetchone()[0])
    assert repository.replay(runtime_run_id="canonical-run@1", store=store) == recorded
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE am_canonical_d2_production_evidence SET content_hash='0'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE am_evidence_attestor_registry_snapshot SET payload_json='{}'")


def test_canonical_evidence_does_not_create_a_run_when_persisted_evidence_is_absent(tmp_path):
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("empty-run", now.isoformat(), now.isoformat(), "git:empty", now.isoformat()))
    repository = CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now))
    with pytest.raises(DataQualityError, match="CANONICAL_D2_ATTESTOR_REGISTRY_MISSING"):
        repository.record(runtime_run_id="empty-run", store=ImmutableDatasetStore(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM am_canonical_d2_production_evidence").fetchone()[0] == 0


def test_self_signed_attestor_registry_is_not_a_trust_root():
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("forged-run", now.isoformat(), now.isoformat(), "git:forged", now.isoformat()))
    private_key = Ed25519PrivateKey.generate()
    payload = {"schema_version": "canonical-evidence-attestor-registry@1", "attestors": []}
    snapshot_id = digest(canonical(payload))
    authorization = attestation_module.registry_authorization_payload(
        authority_id="caller-minted", snapshot_id=snapshot_id, registry_hash=snapshot_id, published_at=now)
    signature = b64encode(private_key.sign(canonical(authorization))).decode()
    authorization_hash = digest(canonical({"payload": authorization, "signature_base64": signature}))
    snapshot_hash = digest(canonical({"registry": payload, "published_at": now.isoformat(),
                                      "registry_authorization_hash": authorization_hash}))
    conn.execute("INSERT INTO am_evidence_attestor_registry_snapshot VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (snapshot_id, json.dumps(payload), snapshot_hash, now.isoformat(), "caller-minted",
                  json.dumps(authorization), signature))
    binding = {"runtime_run_id": "forged-run", "evidence_attestor_registry_snapshot_id": snapshot_id,
               "snapshot_content_hash": snapshot_hash,
               "runtime": {"as_of": now.isoformat(), "information_cutoff": now.isoformat(),
                           "code_revision": "git:forged", "created_at": now.isoformat()},
               "bound_at": now.isoformat()}
    conn.execute("INSERT INTO am_runtime_evidence_attestor_registry VALUES (?, ?, ?, ?)",
                 ("forged-run", snapshot_id, now.isoformat(), digest(canonical(binding))))
    with pytest.raises(DataQualityError, match="CANONICAL_D2_ATTESTOR_REGISTRY_UNTRUSTED"):
        attestation_module.require_runtime_attestor_registry(
            conn=conn, runtime_run_id="forged-run", cutoff=now)


def test_canonical_evidence_rejects_a_stale_fred_curve_despite_valid_raw_provenance(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", (now - timedelta(hours=5)).isoformat()))
    attestor = _attestor(monkeypatch, conn, now - timedelta(hours=5))
    _, estimator = _record_factor_risk(conn, now, attestor)
    _record_provider_snapshot(conn, now, monkeypatch, tmp_path, attestor)
    store = ImmutableDatasetStore(tmp_path / "immutable")
    _estimator_provenance(conn, now, estimator, store, attestor)
    _fred_manifest(conn, now, store, attestor, observation_at=now - timedelta(days=4))
    with pytest.raises(DataQualityError, match="CANONICAL_D2_RISK_FREE_RAW_UNVERIFIED"):
        CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now)).record(
            runtime_run_id="canonical-run@1", store=store)


def test_canonical_evidence_rejects_factor_estimator_without_immutable_raw_lineage(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", (now - timedelta(hours=5)).isoformat()))
    attestor = _attestor(monkeypatch, conn, now - timedelta(hours=5))
    _record_factor_risk(conn, now, attestor)
    _record_provider_snapshot(conn, now, monkeypatch, tmp_path, attestor)
    store = ImmutableDatasetStore(tmp_path / "immutable")
    _fred_manifest(conn, now, store, attestor)
    with pytest.raises(DataQualityError, match="CANONICAL_D2_ESTIMATOR_PROVENANCE_MISSING"):
        CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now)).record(
            runtime_run_id="canonical-run@1", store=store)


def test_canonical_evidence_rejects_model_reviews_without_raw_governance_lineage(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", (now - timedelta(hours=5)).isoformat()))
    attestor = _attestor(monkeypatch, conn, now - timedelta(hours=5))
    _, estimator = _record_factor_risk(conn, now, attestor, external_reviews=False)
    _record_provider_snapshot(conn, now, monkeypatch, tmp_path, attestor)
    store = ImmutableDatasetStore(tmp_path / "immutable")
    _estimator_provenance(conn, now, estimator, store, attestor)
    _fred_manifest(conn, now, store, attestor)
    with pytest.raises(DataQualityError, match="CANONICAL_D2_MODEL_REVIEW_PROVENANCE_MISSING"):
        CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now)).record(
            runtime_run_id="canonical-run@1", store=store)


def test_canonical_evidence_rejects_unattested_caller_authored_fred_raw(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", (now - timedelta(hours=5)).isoformat()))
    attestor = _attestor(monkeypatch, conn, now - timedelta(hours=5))
    _, estimator = _record_factor_risk(conn, now, attestor)
    _record_provider_snapshot(conn, now, monkeypatch, tmp_path, attestor)
    store = ImmutableDatasetStore(tmp_path / "immutable")
    _estimator_provenance(conn, now, estimator, store, attestor)
    _fred_manifest(conn, now, store, attestor, attest_raw=False)
    with pytest.raises(DataQualityError, match="CANONICAL_D2_EVIDENCE_ATTESTATION_MISSING"):
        CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now)).record(
            runtime_run_id="canonical-run@1", store=store)


def test_canonical_evidence_rejects_unattested_provider_accounting_raw(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", (now - timedelta(hours=5)).isoformat()))
    attestor = _attestor(monkeypatch, conn, now - timedelta(hours=5))
    _, estimator = _record_factor_risk(conn, now, attestor)
    _record_provider_snapshot(conn, now, monkeypatch, tmp_path, attestor, attest_raw=False)
    store = ImmutableDatasetStore(tmp_path / "immutable")
    _estimator_provenance(conn, now, estimator, store, attestor)
    _fred_manifest(conn, now, store, attestor)
    with pytest.raises(DataQualityError, match="CANONICAL_D2_EVIDENCE_ATTESTATION_MISSING"):
        CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now)).record(
            runtime_run_id="canonical-run@1", store=store)


def test_canonical_evidence_rejects_receipt_issued_before_raw_response(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", (now - timedelta(hours=5)).isoformat()))
    attestor = _attestor(monkeypatch, conn, now - timedelta(hours=5))
    _, estimator = _record_factor_risk(conn, now, attestor)
    _record_provider_snapshot(conn, now, monkeypatch, tmp_path, attestor)
    store = ImmutableDatasetStore(tmp_path / "immutable")
    _estimator_provenance(conn, now, estimator, store, attestor)
    _fred_manifest(conn, now, store, attestor, attestation_issued_at=now - timedelta(seconds=1))
    with pytest.raises(InvariantViolation, match="CANONICAL_D2_EVIDENCE_ATTESTATION_INVALID"):
        CanonicalD2ProductionEvidenceRepository(conn, FrozenClock(now)).record(
            runtime_run_id="canonical-run@1", store=store)


def test_fred_raw_response_cannot_be_rebound_to_another_runtime(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", (now - timedelta(hours=5)).isoformat()))
    attestor = _attestor(monkeypatch, conn, now - timedelta(hours=5))
    store = ImmutableDatasetStore(tmp_path / "immutable")
    _fred_manifest(conn, now, store, attestor)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("other-run", now.isoformat(), now.isoformat(), "git:other", now.isoformat()))
    raw_id = conn.execute("SELECT raw_response_id FROM am_runtime_fred_risk_free_raw_provenance LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO am_runtime_fred_risk_free_raw_provenance VALUES (?, ?, ?)",
                     ("other-run", "DGS1MO", raw_id))


def test_provider_statement_cannot_be_rebound_to_another_runtime(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    conn = sqlite3.connect(":memory:")
    _schema(conn, now)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("canonical-run@1", now.isoformat(), now.isoformat(), "git:canonical", (now - timedelta(hours=5)).isoformat()))
    attestor = _attestor(monkeypatch, conn, now - timedelta(hours=5))
    snapshot = _record_provider_snapshot(conn, now, monkeypatch, tmp_path, attestor)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("other-run", now.isoformat(), now.isoformat(), "git:other", (now - timedelta(hours=5)).isoformat()))
    row = conn.execute("SELECT provider_contract_id, source_response_id FROM am_provider_accounting_snapshot "
                       "WHERE accounting_snapshot_id=?", (snapshot.accounting_snapshot_id,)).fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO am_provider_accounting_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("provider-accounting:other-run:reused", "other-run", row[0], row[1], "account-2",
                      now.isoformat(), "{}", "f" * 64, now.isoformat()))
