from base64 import b64encode
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.governance import (
    ModelDefinition, ModelRegistry, ModelScope, ModelStatus,
    RuntimeModelRegistryEvidenceRepository,
)
from asset_management.time.clock import FrozenClock, ReplayClock
from asset_management.validation import CanonicalD1RuntimeEvidenceRepository
from asset_management.validation import external_attestation as attestation_module
from asset_management.validation import (
    AcceptanceDecision, CheckEvidence, FeatureStateModelIntegrityGateInput,
    CanonicalD1RuntimeAuthorityVerifier, FeatureStateModelSourceRevisionVerifier,
    REQUIRED_FEATURE_STATE_MODEL_CHECKS,
    evaluate_feature_state_model_integrity_gate,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(minutes=5)
REVISION = "git:" + subprocess.run(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
).stdout.strip()


def _hashed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")), digest(canonical(value))


def _registry() -> ModelRegistry:
    registry = ModelRegistry()
    for index, (model_id, output, scope) in enumerate((
            ("feature", "feature", ModelScope.FEATURE_CALCULATION),
            ("state", "state", ModelScope.STATE_INFERENCE),
            ("pricing", "pricing_baseline_return", ModelScope.PRICING_BASELINE_RETURN),
            ("expectation", "expected_return", ModelScope.EXPECTED_RETURN),
    )):
        model = ModelDefinition(
            model_id, "1", "canonical D1 fixture authority", ("dataset",), (output,), (scope,),
            ("stale-input",), date(2026, 9, 1), date(2026, 12, 31), "model-owner",
        )
        registry.register(model)
        for offset, status in enumerate((ModelStatus.VALIDATED, ModelStatus.APPROVED, ModelStatus.ACTIVE)):
            registry.transition(model.key, status,
                                effective_at=CUTOFF - timedelta(seconds=3 - offset),
                                reason=f"fixture {status.value}", evidence_ids=(f"review-{index}-{offset}",))
    return registry


def _bind_registry(conn):
    clock = ReplayClock(CUTOFF - timedelta(seconds=5))
    evidence = RuntimeModelRegistryEvidenceRepository(conn, clock)
    registry = _registry()
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            evidence.record_review_evidence(evidence_id, model_key=transition.model_key,
                from_status=transition.from_status, to_status=transition.to_status,
                owner=registry.models[transition.model_key].owner, evidence={"fixture": "review"})
    clock.advance_to(CUTOFF - timedelta(seconds=1))
    snapshot = evidence.publish_snapshot(registry)
    clock.advance_to(CUTOFF)
    evidence.bind_runtime_run("runtime@1", snapshot)


def _repository(tmp_path, *, states=True):
    conn = sqlite3.connect(":memory:")
    Migrator(conn, FrozenClock(NOW)).migrate(load_migration_catalog(ROOT / "schemas"))
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("runtime@1", NOW.isoformat(), CUTOFF.isoformat(), REVISION,
                  (CUTOFF - timedelta(minutes=1)).isoformat()))
    store = ImmutableDatasetStore(tmp_path / "immutable")
    manifest = store.write(
        [{"instrument_id": "ETF", "value": "100"}], layer="bronze", source="fixture-provider",
        dataset="canonical-inputs", schema_version="fixture@1", retrieved_at=CUTOFF,
        available_at=CUTOFF, provider_timestamp=CUTOFF,
        license_tag="purpose=test;redistribution=forbidden;retention=ephemeral",
        code_revision=REVISION, request_hash="b" * 64, quality_status="RAW",
    )
    conn.execute("INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
                 ("ingestion@1", "runtime@1", "fixture-provider", CUTOFF.isoformat(), NOW.isoformat()))
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (manifest.manifest_id, "ingestion@1", manifest.layer, manifest.dataset,
                  "immutable://fixture", manifest.content_sha256, CUTOFF.isoformat(), CUTOFF.isoformat(),
                  manifest.schema_version, manifest.row_count))
    feature_payload, feature_hash = _hashed({"feature_values": {"ETF": "1"}})
    conn.execute("INSERT INTO am_feature_run VALUES (?, ?, ?, ?, ?, ?)",
                 ("feature@1", "runtime@1", manifest.manifest_id, "feature@1", feature_payload, feature_hash))
    if states:
        state_payload, state_hash = _hashed({"state": "CALM"})
        conn.execute("INSERT INTO am_state_run VALUES (?, ?, ?, ?, ?)",
                     ("state@1", "feature@1", "state@1", state_payload, state_hash))
        pricing_payload, pricing_hash = _hashed({"pricing_outputs": {"ETF": "0.01"}})
        conn.execute("INSERT INTO am_pricing_run VALUES (?, ?, ?, ?, ?)",
                     ("pricing@1", "state@1", "pricing@1", pricing_payload, pricing_hash))
        expectation_payload, expectation_hash = _hashed({"forecast_values": {"ETF": "0.02"}})
        conn.execute("INSERT INTO am_expectation_run VALUES (?, ?, ?, ?, ?)",
                     ("expectation@1", "pricing@1", "expectation@1", expectation_payload, expectation_hash))
    _bind_registry(conn)
    return conn, store


def _copy_signed_runtime_registry(monkeypatch, conn):
    """Install a valid but reusable registry, without any D1 bundle attestation."""
    authority_id = "test-d1-registry-governance"
    authority_key = Ed25519PrivateKey.generate()
    authority_der = authority_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    authority = attestation_module.RegistryGovernanceAuthority(
        authority_id=authority_id, kms_key_version_resource=authority_id,
        signing_algorithm="EC_SIGN_ED25519", public_key_der_spki_base64=b64encode(authority_der).decode(),
        public_key_fingerprint_sha256=sha256(authority_der).hexdigest(),
        effective_from_utc="2020-01-01T00:00:00+00:00", effective_to_utc=None,
    )
    attestor_key = Ed25519PrivateKey.generate()
    payload = {"schema_version": "canonical-evidence-attestor-registry@1", "attestors": [{
        "attestor_id": "test-d1-attestor", "algorithm": "ed25519",
        "public_key_base64": b64encode(attestor_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)).decode(),
        "effective_from_utc": "2020-01-01T00:00:00+00:00", "effective_to_utc": None,
    }]}
    snapshot_id = digest(canonical(payload))
    published_at = CUTOFF
    authorization = attestation_module.registry_authorization_payload(
        authority_id=authority_id, snapshot_id=snapshot_id, registry_hash=snapshot_id,
        published_at=published_at)
    authority_signature_bytes = authority_key.sign(canonical(authorization))
    authority_signature = b64encode(authority_signature_bytes).decode()
    authorization_hash = digest(canonical({"payload": authorization,
                                           "signature_base64": authority_signature}))
    monkeypatch.setattr(attestation_module, "_REGISTRY_GOVERNANCE_AUTHORITY_HISTORY", (
        replace(authority, authorization_evidence=attestation_module.RegistryAuthorizationEvidenceBinding(
            snapshot_id=snapshot_id, registry_hash=snapshot_id,
            authorization_payload_sha256=digest(canonical(authorization)),
            signature_sha256=sha256(authority_signature_bytes).hexdigest(),
            verification_evidence_sha256=digest(canonical({"test": "registry-copy"})),
            published_at_utc=published_at.isoformat(), attestor_id="test-d1-attestor",
            attestor_public_key_base64=payload["attestors"][0]["public_key_base64"],
            attestor_effective_from_utc="2020-01-01T00:00:00+00:00",
            attestor_effective_to_utc=None)),
    ))
    snapshot_hash = digest(canonical({"registry": payload, "published_at": published_at.isoformat(),
                                      "registry_authorization_hash": authorization_hash}))
    conn.execute("INSERT INTO am_evidence_attestor_registry_snapshot VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (snapshot_id, json.dumps(payload, sort_keys=True, separators=(",", ":")), snapshot_hash,
                  published_at.isoformat(), authority_id,
                  json.dumps(authorization, sort_keys=True, separators=(",", ":")), authority_signature))
    runtime = conn.execute(
        "SELECT as_of_utc, information_cutoff_utc, code_revision, created_at_utc "
        "FROM am_runtime_run WHERE runtime_run_id='runtime@1'").fetchone()
    binding = {"runtime_run_id": "runtime@1", "evidence_attestor_registry_snapshot_id": snapshot_id,
               "snapshot_content_hash": snapshot_hash,
               "runtime": {"as_of": str(runtime[0]), "information_cutoff": str(runtime[1]),
                           "code_revision": str(runtime[2]), "created_at": str(runtime[3])},
               "bound_at": published_at.isoformat()}
    conn.execute("INSERT INTO am_runtime_evidence_attestor_registry VALUES (?, ?, ?, ?)",
                 ("runtime@1", snapshot_id, published_at.isoformat(), digest(canonical(binding))))
    return snapshot_id, snapshot_hash


def test_records_and_replays_only_one_existing_runtime_bundle(tmp_path):
    conn, store = _repository(tmp_path)
    repository = CanonicalD1RuntimeEvidenceRepository(conn, FrozenClock(NOW))

    recorded = repository.record(runtime_run_id="runtime@1", store=store)
    replayed = repository.replay(runtime_run_id="runtime@1", store=store)

    assert replayed == recorded
    assert store.layout.resolve("catalog", f"canonical-d1-runtime-evidence/{recorded.catalog_object_id}.json").is_file()
    assert conn.execute("SELECT runtime_run_id FROM am_canonical_d1_runtime_evidence").fetchone() == ("runtime@1",)


def test_fixture_runtime_bundle_cannot_pass_without_external_canonical_authority(tmp_path):
    conn, store = _repository(tmp_path)
    repository = CanonicalD1RuntimeEvidenceRepository(conn, FrozenClock(NOW))
    bundle = repository.record(runtime_run_id="runtime@1", store=store)
    source = FeatureStateModelSourceRevisionVerifier(ROOT)
    _, tree = source.verify(REVISION) or (None, None)
    assert tree is not None
    checks = {}
    for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS:
        identifier = store.catalog("feature-state-model-gate-evidence", {
            "schema_version": "feature-state-model-gate-evidence@4", "check_name": name,
            "code_revision": REVISION, "source_tree": tree, "runtime_run_id": "runtime@1",
            "runtime_code_revision": bundle.code_revision,
            "canonical_runtime_evidence_hash": bundle.content_hash,
            "canonical_runtime_catalog_object_id": bundle.catalog_object_id,
            "attestor_registry_snapshot_id": "a" * 64,
            "attestor_registry_content_hash": "b" * 64,
            "runtime_attestation_id": "c" * 64,
            "runtime_attestation_hash": "d" * 64,
            "canonical_store_uri": "gs://canonical-fixture-evidence/d1",
        })
        checks[name] = CheckEvidence(True, (f"sha256:{identifier}",))
    result = evaluate_feature_state_model_integrity_gate(
        FeatureStateModelIntegrityGateInput(NOW, "runtime@1", REVISION, REVISION, checks),
        evidence_store=store, source_revision_verifier=source, runtime_evidence_repository=repository,
        runtime_authority_verifier=CanonicalD1RuntimeAuthorityVerifier(repository),
    )
    assert result.decision is AcceptanceDecision.FAIL
    assert "CANONICAL_RUNTIME_AUTHORITY_UNVERIFIED" in result.reason_codes


def test_copied_signed_registry_cannot_authorize_a_local_runtime_bundle(tmp_path, monkeypatch):
    conn, store = _repository(tmp_path)
    repository = CanonicalD1RuntimeEvidenceRepository(conn, FrozenClock(NOW))
    bundle = repository.record(runtime_run_id="runtime@1", store=store)
    snapshot_id, snapshot_hash = _copy_signed_runtime_registry(monkeypatch, conn)
    source = FeatureStateModelSourceRevisionVerifier(ROOT)
    _, tree = source.verify(REVISION) or (None, None)
    assert tree is not None
    checks = {}
    for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS:
        identifier = store.catalog("feature-state-model-gate-evidence", {
            "schema_version": "feature-state-model-gate-evidence@4", "check_name": name,
            "code_revision": REVISION, "source_tree": tree, "runtime_run_id": "runtime@1",
            "runtime_code_revision": bundle.code_revision,
            "canonical_runtime_evidence_hash": bundle.content_hash,
            "canonical_runtime_catalog_object_id": bundle.catalog_object_id,
            "attestor_registry_snapshot_id": snapshot_id,
            "attestor_registry_content_hash": snapshot_hash,
            "runtime_attestation_id": "c" * 64,
            "runtime_attestation_hash": "d" * 64,
            "canonical_store_uri": "gs://canonical-fixture-evidence/d1",
        })
        checks[name] = CheckEvidence(True, (f"sha256:{identifier}",))
    result = evaluate_feature_state_model_integrity_gate(
        FeatureStateModelIntegrityGateInput(NOW, "runtime@1", REVISION, REVISION, checks),
        evidence_store=store, source_revision_verifier=source, runtime_evidence_repository=repository,
        runtime_authority_verifier=CanonicalD1RuntimeAuthorityVerifier(repository),
    )
    assert result.decision is AcceptanceDecision.FAIL
    assert "CANONICAL_RUNTIME_AUTHORITY_UNVERIFIED" in result.reason_codes


def test_missing_state_is_blocked_and_cannot_be_recorded(tmp_path):
    conn, store = _repository(tmp_path, states=False)
    repository = CanonicalD1RuntimeEvidenceRepository(conn, FrozenClock(NOW))

    with pytest.raises(DataQualityError, match="CANONICAL_D1_STATE_EVIDENCE_MISSING"):
        repository.record(runtime_run_id="runtime@1", store=store)


def test_source_manifest_hash_drift_is_rejected_on_replay(tmp_path):
    conn, store = _repository(tmp_path)
    repository = CanonicalD1RuntimeEvidenceRepository(conn, FrozenClock(NOW))
    repository.record(runtime_run_id="runtime@1", store=store)
    conn.execute("DROP TRIGGER am_feature_run_no_update")
    conn.execute("UPDATE am_feature_run SET content_hash=? WHERE feature_run_id='feature@1'", ("f" * 64,))

    with pytest.raises(InvariantViolation, match="CANONICAL_D1_FEATURE_PAYLOAD_INVALID"):
        repository.replay(runtime_run_id="runtime@1", store=store)


def test_manifest_received_after_cutoff_is_rejected_as_non_pit_evidence(tmp_path):
    conn, store = _repository(tmp_path)
    repository = CanonicalD1RuntimeEvidenceRepository(conn, FrozenClock(NOW))
    conn.execute("DROP TRIGGER am_manifest_no_update")
    conn.execute("UPDATE am_dataset_manifest SET received_at_utc=?", (NOW.isoformat(),))

    with pytest.raises(DataQualityError, match="CANONICAL_D1_MANIFEST_NOT_POINT_IN_TIME"):
        repository.record(runtime_run_id="runtime@1", store=store)


def test_orphan_feature_or_state_cannot_be_hidden_by_a_valid_chain(tmp_path):
    conn, store = _repository(tmp_path)
    repository = CanonicalD1RuntimeEvidenceRepository(conn, FrozenClock(NOW))
    payload, content_hash = _hashed({"feature_values": {"ETF": "2"}})
    manifest_id = conn.execute("SELECT dataset_manifest_id FROM am_feature_run WHERE feature_run_id='feature@1'").fetchone()[0]
    conn.execute("INSERT INTO am_feature_run VALUES (?, ?, ?, ?, ?, ?)",
                 ("feature@2", "runtime@1", manifest_id, "feature@1", payload, content_hash))
    with pytest.raises(DataQualityError, match="CANONICAL_D1_STATE_COVERAGE_MISSING"):
        repository.record(runtime_run_id="runtime@1", store=store)

    conn, store = _repository(tmp_path / "state")
    repository = CanonicalD1RuntimeEvidenceRepository(conn, FrozenClock(NOW))
    payload, content_hash = _hashed({"state": "STRESSED"})
    conn.execute("INSERT INTO am_state_run VALUES (?, ?, ?, ?, ?)",
                 ("state@2", "feature@1", "state@1", payload, content_hash))
    with pytest.raises(DataQualityError, match="CANONICAL_D1_CALCULATION_COVERAGE_MISSING"):
        repository.record(runtime_run_id="runtime@1", store=store)


def test_replay_only_fails_if_the_catalog_object_was_removed_instead_of_republishing(tmp_path):
    conn, store = _repository(tmp_path)
    repository = CanonicalD1RuntimeEvidenceRepository(conn, FrozenClock(NOW))
    recorded = repository.record(runtime_run_id="runtime@1", store=store)
    catalog = store.layout.resolve("catalog", f"canonical-d1-runtime-evidence/{recorded.catalog_object_id}.json")
    catalog.unlink()

    with pytest.raises(DataQualityError, match="CANONICAL_D1_EVIDENCE_CATALOG_MISSING"):
        repository.replay(runtime_run_id="runtime@1", store=store)
