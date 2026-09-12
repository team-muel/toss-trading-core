from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
import sqlite3

import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.data.repositories import SQLiteTemporalObservationStore
from asset_management.domain.economics import CurrencyBasis
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.governance import (
    ModelDefinition, ModelRegistry, ModelScope, ModelStatus,
    RuntimeModelRegistryEvidenceRepository,
)
from asset_management.reference.calendars import SessionRepository
from asset_management.risk import (
    FactorExposure, FactorRiskCalculationRepository, MissingPolicy, SpecificRiskPolicy,
)
from asset_management.time.asof import AsOfContext
from asset_management.time.clock import FrozenClock, ReplayClock


ROOT = __import__("pathlib").Path(__file__).parents[1]
NOW = datetime(2026, 9, 11, 9, tzinfo=timezone.utc)
D = Decimal
MANIFEST_HASH = "a" * 64


def repository():
    conn = sqlite3.connect(":memory:")
    Migrator(conn, FrozenClock(NOW)).migrate(load_migration_catalog(ROOT / "schemas"))
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("risk-runtime@1", NOW.isoformat(), NOW.isoformat(), "git:risk", NOW.isoformat()))
    conn.execute("INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
                 ("ingestion@1", "risk-runtime@1", "returns", NOW.isoformat(), NOW.isoformat()))
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 ("manifest@1", "ingestion@1", "silver", "total-return", "memory://returns",
                  MANIFEST_HASH, NOW.isoformat(), NOW.isoformat(), "returns@1", 6))
    return conn, FactorRiskCalculationRepository(conn, FrozenClock(NOW))


def context():
    return AsOfContext("risk-runtime@1", NOW, NOW, "policy@1", "parameters@1", "git:risk")


def append_returns(conn):
    store = SQLiteTemporalObservationStore(conn)
    for offset, values in enumerate(((".01", ".02"), ("-.01", ".00"), (".02", "-.01"))):
        instant = NOW - timedelta(days=3 - offset)
        for instrument, value in zip(("A", "B"), values):
            store.append(entity_id=instrument, field="risk_return:total", value={
                "return": value, "calendar_id": "XNYS", "currency_basis": "BASE", "total_return": True,
            }, reference_period=instant.date().isoformat(), event_time=instant,
                scheduled_release_at=None, official_release_at=instant, source_timestamp=instant,
                received_at=instant, available_at=instant, ingested_at=instant, revised_at=None,
                source_timezone="UTC", schema_version="risk-return@1",
                dataset_manifest_id="manifest@1")
    sessions = SessionRepository(conn)
    for offset in range(3):
        instant = NOW - timedelta(days=3 - offset)
        sessions.record(exchange="XNYS", local_date=instant.date().isoformat(), timezone="UTC",
            session_status="OPEN", regular_open=instant.replace(hour=0), regular_close=instant.replace(hour=1),
            effective_from=instant.replace(hour=0), available_at=instant, source="calendar@1")


def estimator_inputs():
    return dict(
        exposures=(FactorExposure("A", (D(1),), NOW, NOW, "exposure@1"),
                   FactorExposure("B", (D(".5"),), NOW, NOW, "exposure@1")),
        factor_matrix=((D(".04"),),), residual_variance=(D(".02"), D(".03")),
        residual_history=(30, 30), residual_serial_correlation=(D(".1"), D(".2")),
        residual_heteroskedasticity=(D(".1"), D(".2")),
        policy=SpecificRiskPolicy(20, D(".01"), D(".25"), D(".10"), "specific-risk@1"))


def authorization(conn):
    registry = ModelRegistry()
    model = ModelDefinition("FACTOR_RISK", "1", "factor risk", ("total_return", "exposure"),
        ("factor_risk",), (ModelScope.RISK_ESTIMATION,), ("stale input",),
        NOW.date() - timedelta(days=2), NOW.date() + timedelta(days=2), "risk-owner")
    registry.register(model)
    for index, status in enumerate((ModelStatus.VALIDATED, ModelStatus.APPROVED, ModelStatus.ACTIVE)):
        registry.transition(model.key, status, effective_at=NOW - timedelta(minutes=4 - index),
                            reason="review", evidence_ids=(f"risk-review-{index}",))
    clock = ReplayClock(NOW - timedelta(minutes=5))
    evidence = RuntimeModelRegistryEvidenceRepository(conn, clock)
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            evidence.record_review_evidence(evidence_id, model_key=transition.model_key,
                from_status=transition.from_status, to_status=transition.to_status,
                owner="risk-owner", evidence={"review": "approved"})
    clock.advance_to(NOW - timedelta(seconds=1))
    snapshot = evidence.publish_snapshot(registry)
    clock.advance_to(NOW)
    evidence.bind_runtime_run("risk-runtime@1", snapshot)
    return evidence, evidence.authorize("risk-runtime@1", model_key="FACTOR_RISK@1", scope=ModelScope.RISK_ESTIMATION)


def calculation_inputs(conn, evidence, *, estimator_changes=None, max_exposure_age_days=0,
                       max_return_age_days=3):
    model_evidence, token = authorization(conn)
    estimator = estimator_inputs() | (estimator_changes or {})
    estimator_id = evidence.record_estimator_evidence(context=context(), model_key="FACTOR_RISK@1",
        model_registry_evidence=model_evidence, runtime_authorization=token,
        maximum_exposure_age_days=max_exposure_age_days, maximum_return_age_days=max_return_age_days,
        **estimator)
    return dict(instruments=("A", "B"), return_field="risk_return:total", calendar_id="XNYS",
        currency_basis=CurrencyBasis.BASE, missing_policy=MissingPolicy.FAIL,
        estimator_evidence_id=estimator_id, model_key="FACTOR_RISK@1",
        model_registry_evidence=model_evidence, runtime_authorization=token)


def test_persisted_raw_return_to_factor_risk_replays_deterministically():
    conn, evidence = repository()
    append_returns(conn)
    arguments = calculation_inputs(conn, evidence)
    persisted = evidence.calculate(context=context(), **arguments)
    assert evidence.require(persisted, model_registry_evidence=arguments["model_registry_evidence"], runtime_authorization=arguments["runtime_authorization"]) == persisted.assessment
    assert evidence.replay(persisted.factor_risk_calculation_id,
                           model_registry_evidence=arguments["model_registry_evidence"]) == persisted
    assert persisted.assessment.covariance
    assert conn.execute("SELECT COUNT(*) FROM am_factor_risk_calculation").fetchone()[0] == 1


def test_replay_fails_closed_when_persisted_source_evidence_is_deleted():
    conn, evidence = repository()
    append_returns(conn)
    arguments = calculation_inputs(conn, evidence)
    persisted = evidence.calculate(context=context(), **arguments)
    conn.execute("DROP TRIGGER am_temporal_observation_delete_block")
    conn.execute("DELETE FROM am_temporal_observation WHERE observation_id=(SELECT observation_id FROM am_temporal_observation LIMIT 1)")
    with pytest.raises(InvariantViolation, match="FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"):
        evidence.require(persisted, model_registry_evidence=arguments["model_registry_evidence"], runtime_authorization=arguments["runtime_authorization"])


def test_calculation_never_substitutes_fixture_or_tampered_record_for_persisted_evidence():
    conn, evidence = repository()
    with pytest.raises(InvariantViolation, match="FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"):
        evidence.calculate(context=context(), instruments=("A",), return_field="risk_return:total", calendar_id="XNYS",
            currency_basis=CurrencyBasis.BASE, missing_policy=MissingPolicy.FAIL, estimator_evidence_id="a" * 64,
            model_key="FACTOR_RISK@1", model_registry_evidence=None, runtime_authorization=None)
    append_returns(conn)
    arguments = calculation_inputs(conn, evidence)
    persisted = evidence.calculate(context=context(), **arguments)
    conn.execute("DROP TRIGGER am_factor_risk_calculation_no_update")
    conn.execute("UPDATE am_factor_risk_calculation SET assessment_payload_json='{}'")
    with pytest.raises(InvariantViolation, match="FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"):
        evidence.require(persisted, model_registry_evidence=arguments["model_registry_evidence"], runtime_authorization=arguments["runtime_authorization"])


def test_replay_rehashes_current_raw_observation_rows():
    conn, evidence = repository()
    append_returns(conn)
    arguments = calculation_inputs(conn, evidence)
    persisted = evidence.calculate(context=context(), **arguments)
    conn.execute("DROP TRIGGER am_temporal_observation_update_block")
    conn.execute("UPDATE am_temporal_observation SET value_json=? WHERE observation_id=(SELECT observation_id FROM am_temporal_observation LIMIT 1)",
                 ('{"calendar_id":"XNYS","currency_basis":"BASE","return":".99","total_return":true}',))
    with pytest.raises(InvariantViolation, match="FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"):
        evidence.require(persisted, model_registry_evidence=arguments["model_registry_evidence"], runtime_authorization=arguments["runtime_authorization"])


def test_persisted_estimator_freshness_policy_blocks_stale_exposures():
    conn, evidence = repository()
    append_returns(conn)
    stale = (FactorExposure("A", (D(1),), NOW - timedelta(days=1), NOW, "exposure@1"),
             FactorExposure("B", (D(".5"),), NOW, NOW, "exposure@1"))
    arguments = calculation_inputs(conn, evidence, estimator_changes={"exposures": stale})
    with pytest.raises(InvariantViolation, match="FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"):
        evidence.calculate(context=context(), **arguments)


def test_model_authorization_must_bind_this_runtime_and_persisted_store():
    conn, evidence = repository()
    append_returns(conn)
    arguments = calculation_inputs(conn, evidence)
    with pytest.raises(InvariantViolation, match="FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"):
        evidence.calculate(context=context(), **(arguments | {
            "runtime_authorization": replace(arguments["runtime_authorization"], runtime_run_id="other-runtime"),
        }))
    foreign = RuntimeModelRegistryEvidenceRepository(sqlite3.connect(":memory:"), FrozenClock(NOW))
    with pytest.raises(InvariantViolation, match="FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"):
        evidence.calculate(context=context(), **(arguments | {"model_registry_evidence": foreign}))


def test_estimator_evidence_recorded_after_cutoff_is_rejected():
    conn, _ = repository()
    model_evidence, token = authorization(conn)
    late = FactorRiskCalculationRepository(conn, FrozenClock(NOW + timedelta(seconds=1)))
    with pytest.raises(InvariantViolation, match="FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED"):
        late.record_estimator_evidence(context=context(), model_key="FACTOR_RISK@1",
            model_registry_evidence=model_evidence, runtime_authorization=token,
            maximum_exposure_age_days=0, maximum_return_age_days=3, **estimator_inputs())
