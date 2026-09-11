from datetime import date, datetime, timedelta, timezone
import sqlite3

import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import (
    ModelDefinition, ModelRegistry, ModelScope, ModelStatus,
    RuntimeModelRegistryEvidenceRepository,
)
from asset_management.orchestration.runtime import ApplicationRuntime
from asset_management.config.validation import validate_startup_config
from asset_management.time.clock import FrozenClock, ReplayClock


ROOT = __import__("pathlib").Path(__file__).parents[1]
NOW = datetime(2026, 9, 11, 9, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(minutes=5)


def active_v2_registry() -> ModelRegistry:
    registry = ModelRegistry()
    model = ModelDefinition(
        "CAPM", "2", "equity pricing baseline", ("risk_free", "beta", "market_premium"),
        ("pricing_baseline_return",), (ModelScope.PRICING_BASELINE_RETURN,),
        ("stale_risk_free", "unstable_beta"), date(2026, 9, 1), date(2026, 12, 31),
        "economic-model-owner",
    )
    registry.register(model)
    for index, status in enumerate((ModelStatus.VALIDATED, ModelStatus.APPROVED, ModelStatus.ACTIVE)):
        registry.transition(model.key, status, effective_at=CUTOFF - timedelta(seconds=3 - index),
                            reason=f"reviewed promotion to {status.value}",
                            evidence_ids=(f"review:capm-v2:{index}",))
    return registry


def repository() -> tuple[sqlite3.Connection, RuntimeModelRegistryEvidenceRepository, ReplayClock]:
    conn = sqlite3.connect(":memory:")
    Migrator(conn, FrozenClock(NOW)).migrate(load_migration_catalog(ROOT / "schemas"))
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("runtime@1", NOW.isoformat(), CUTOFF.isoformat(), "git:runtime", CUTOFF.isoformat()))
    clock = ReplayClock(CUTOFF - timedelta(seconds=5))
    return conn, RuntimeModelRegistryEvidenceRepository(conn, clock), clock


def review_and_publish(evidence, clock, registry):
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            evidence.record_review_evidence(
                evidence_id, model_key=transition.model_key, from_status=transition.from_status,
                to_status=transition.to_status, owner=registry.models[transition.model_key].owner,
                evidence={"review": "approved", "transition": transition.to_status.value},
            )
    clock.advance_to(CUTOFF - timedelta(seconds=1))
    return evidence.publish_snapshot(registry)


def test_runtime_authorization_requires_preexisting_immutable_registry_snapshot():
    _, evidence, clock = repository()
    registry = active_v2_registry()
    snapshot = review_and_publish(evidence, clock, registry)
    clock.advance_to(CUTOFF)
    binding = evidence.bind_runtime_run("runtime@1", snapshot)

    authorization = evidence.authorize("runtime@1", model_key="CAPM@2",
                                      scope=ModelScope.PRICING_BASELINE_RETURN)
    selected = evidence.require_authorization(
        authorization, model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN, at=NOW,
    )
    assert authorization.binding_hash == binding
    assert selected.registry_hash == registry.registry_hash


def test_caller_created_active_registry_cannot_authorize_without_runtime_binding():
    _, evidence, _ = repository()
    forged = active_v2_registry()
    assert forged.authorize("CAPM@2", ModelScope.PRICING_BASELINE_RETURN, at=CUTOFF)
    with pytest.raises(InvariantViolation, match="MODEL_RUNTIME_BINDING_MISSING"):
        evidence.authorize("runtime@1", model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN)


def test_snapshot_published_after_information_cutoff_is_rejected():
    _, evidence, clock = repository()
    registry = active_v2_registry()
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            evidence.record_review_evidence(evidence_id, model_key=transition.model_key,
                from_status=transition.from_status, to_status=transition.to_status,
                owner=registry.models[transition.model_key].owner, evidence={"review": "approved"})
    clock.advance_to(NOW)
    snapshot = evidence.publish_snapshot(registry)
    with pytest.raises(InvariantViolation, match="MODEL_RUNTIME_SNAPSHOT_NOT_POINT_IN_TIME"):
        evidence.bind_runtime_run("runtime@1", snapshot)


def test_snapshot_and_runtime_binding_are_append_only_and_hash_verified():
    conn, evidence, clock = repository()
    snapshot = review_and_publish(evidence, clock, active_v2_registry())
    clock.advance_to(CUTOFF)
    evidence.bind_runtime_run("runtime@1", snapshot)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE am_runtime_model_registry SET content_hash='f' WHERE runtime_run_id='runtime@1'")
    conn.execute("DROP TRIGGER am_runtime_model_registry_no_update")
    conn.execute("UPDATE am_runtime_model_registry SET content_hash='f' WHERE runtime_run_id='runtime@1'")
    with pytest.raises(InvariantViolation, match="MODEL_RUNTIME_BINDING_INVALID"):
        evidence.authorize("runtime@1", model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN)


def test_runtime_snapshot_cannot_be_backdated_or_bound_after_pipeline_evidence():
    conn, evidence, clock = repository()
    snapshot = review_and_publish(evidence, clock, active_v2_registry())
    conn.execute("DROP TRIGGER am_model_registry_snapshot_no_update")
    conn.execute("UPDATE am_model_registry_snapshot SET published_at_utc=?", (
        (CUTOFF - timedelta(seconds=2)).isoformat(),))
    clock.advance_to(CUTOFF)
    with pytest.raises(InvariantViolation, match="MODEL_RUNTIME_EVIDENCE_INVALID"):
        evidence.bind_runtime_run("runtime@1", snapshot)
    conn, evidence, clock = repository()
    snapshot = review_and_publish(evidence, clock, active_v2_registry())
    clock.advance_to(CUTOFF)
    evidence.bind_runtime_run("runtime@1", snapshot)
    conn.execute("DROP TRIGGER am_runtime_run_no_update")
    conn.execute("UPDATE am_runtime_run SET information_cutoff_utc=?", (NOW.isoformat(),))
    with pytest.raises(InvariantViolation, match="MODEL_RUNTIME_BINDING_INVALID"):
        evidence.authorize("runtime@1", model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN)


def test_review_evidence_timestamp_and_existence_are_revalidated():
    conn, evidence, clock = repository()
    registry = active_v2_registry()
    clock.advance_to(CUTOFF - timedelta(seconds=1))
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            evidence.record_review_evidence(evidence_id, model_key=transition.model_key,
                from_status=transition.from_status, to_status=transition.to_status,
                owner=registry.models[transition.model_key].owner, evidence={"review": "approved"})
    conn.execute("DROP TRIGGER am_model_governance_review_evidence_no_update")
    conn.execute("UPDATE am_model_governance_review_evidence SET recorded_at_utc=?", (
        (CUTOFF - timedelta(seconds=5)).isoformat(),))
    with pytest.raises(InvariantViolation, match="MODEL_REVIEW_EVIDENCE_INVALID"):
        evidence.publish_snapshot(registry)

    conn, evidence, clock = repository()
    snapshot = review_and_publish(evidence, clock, active_v2_registry())
    clock.advance_to(CUTOFF)
    evidence.bind_runtime_run("runtime@1", snapshot)
    conn.execute("DROP TRIGGER am_model_governance_review_evidence_no_delete")
    conn.execute("DELETE FROM am_model_governance_review_evidence")
    with pytest.raises(InvariantViolation, match="MODEL_RUNTIME_EVIDENCE_INVALID"):
        evidence.authorize("runtime@1", model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN)


def test_runtime_registry_must_be_selected_before_any_pipeline_stage_evidence():
    conn, evidence, clock = repository()
    snapshot = review_and_publish(evidence, clock, active_v2_registry())
    conn.execute("DROP TRIGGER am_pipeline_stage_artifact_guard")
    conn.execute("INSERT INTO am_pipeline_stage_evidence VALUES (?, ?, ?, ?, ?)",
                 ("runtime@1", 1, "INVESTMENT_POLICY", "policy@1", "a" * 64))
    clock.advance_to(CUTOFF)
    with pytest.raises(InvariantViolation, match="MODEL_RUNTIME_SNAPSHOT_NOT_POINT_IN_TIME"):
        evidence.bind_runtime_run("runtime@1", snapshot)


def test_application_runtime_exposes_model_authority_only_after_booted_evidence_binding():
    _, evidence, clock = repository()
    runtime = ApplicationRuntime(
        validate_startup_config({
            "runtime_mode": "READ_ONLY", "base_currency": "USD", "reporting_currency": "KRW",
            "enabled_data_sources": ["TOSS"], "data_sources": {"TOSS": {"kind": "broker", "schema_version": "1"}},
            "parameter_set": {"id": "read-only@1", "status": "APPROVED"},
            "risk_limits": {"max_open_orders": "1"}, "live_trading_enabled": False,
        }),
        clock, model_registry_evidence=evidence,
    )
    registry = active_v2_registry()
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            runtime.record_model_review_evidence(evidence_id, model_key=transition.model_key,
                from_status=transition.from_status, to_status=transition.to_status,
                owner=registry.models[transition.model_key].owner, evidence={"review": "approved"})
    clock.advance_to(CUTOFF - timedelta(seconds=1))
    snapshot = runtime.publish_model_registry_snapshot(registry)
    clock.advance_to(CUTOFF)
    runtime.select_model_registry("runtime@1", snapshot)
    token = runtime.authorize_runtime_model(
        "runtime@1", model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN)
    assert token.model_registry_snapshot_id == snapshot
