from datetime import datetime, timedelta, timezone
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.data.asof_query import AsOfRepository
from asset_management.data.immutable import canonical, digest
from asset_management.data.repositories import SQLiteTemporalObservationStore
from asset_management.domain.economics import CurrencyBasis
from asset_management.domain.errors import DataQualityError
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.domain.scalars import Currency
from asset_management.expectations import AssetClass, ComponentForecastAssembler, CommodityStructure
from asset_management.expectations.engine import COMPONENTS, expected_return
from asset_management.governance import (
    ModelDefinition, ModelRegistry, ModelScope, ModelStatus,
    RuntimeModelRegistryEvidenceRepository,
)
from asset_management.time.asof import AsOfContext
from asset_management.time.clock import FrozenClock, ReplayClock


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
CONTEXT = AsOfContext("run@1", NOW, NOW, "policy@1", "parameters@1", "git:test")
VALIDITY = SignalValidity(252, 252, NOW + timedelta(days=1), DecayProfile.STEP)
MANIFEST = "a" * 64


def repository(*, runtime="run@1", semantic_type="ECONOMIC_COMPONENT_INPUT", fresh_until=None,
               conflicting=False, manifest_runtime="run@1", payload_cutoff=None,
               duplicate_exposure=False, numeric_observation_ids=False):
    conn = sqlite3.connect(":memory:")
    Migrator(conn, FrozenClock(NOW)).migrate(load_migration_catalog(ROOT / "schemas"))
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("run@1", NOW.isoformat(), NOW.isoformat(), "git:test", NOW.isoformat()))
    if manifest_runtime != "run@1":
        conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                     (manifest_runtime, NOW.isoformat(), NOW.isoformat(), "git:other", NOW.isoformat()))
    conn.execute("INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
                 ("ingestion@1", manifest_runtime, "economic", NOW.isoformat(), NOW.isoformat()))
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (MANIFEST, "ingestion@1", "silver", "economic-component-input", "memory://economic",
                  MANIFEST, NOW.isoformat(), NOW.isoformat(), "economic@1", 1))
    store = SQLiteTemporalObservationStore(conn)
    payload = {
        "semantic_type": semantic_type, "runtime_run_id": runtime,
        "information_cutoff_utc": (payload_cutoff or NOW).isoformat(), "currency": "USD", "currency_basis": "BASE",
        "compounding": "ANNUAL_EFFECTIVE", "model_version": "economic@1",
        "fresh_until_utc": (fresh_until or NOW + timedelta(minutes=1)).isoformat(),
        "annualized_return": "0.10", "prior_annualized_return": "0.02",
        "annualized_uncertainty": "0.03", "confidence": "0.8",
    }
    for index, name in enumerate(sorted(set().union(*COMPONENTS.values()))):
        values = dict(entity_id="SPY", field=f"economic-input/{name}",
                      value=payload | {"economic_exposure_id": "shared" if duplicate_exposure else f"exposure:{name}"},
                      reference_period=NOW.date().isoformat(), event_time=NOW,
                      scheduled_release_at=None, official_release_at=NOW, source_timestamp=NOW,
                      received_at=NOW, available_at=NOW, ingested_at=NOW, revised_at=None,
                      source_timezone="UTC", schema_version="economic-component-input@1",
                      dataset_manifest_id=MANIFEST)
        if numeric_observation_ids:
            values["observation_id"] = str(100000000000000000 + index)
        store.append(**values)
        if conflicting and name == COMPONENTS[AssetClass.EQUITY_ETF][0]:
            conn.execute("DROP TRIGGER am_temporal_observation_initial_vintage_guard")
            store.append(**(values | {"value": payload | {"annualized_return": "0.11"}}))
    registry = ModelRegistry()
    model = ModelDefinition("economic", "1", "PIT economic component generation",
                            ("economic-component-input",), ("forecast-component",),
                            (ModelScope.EXPECTED_RETURN,), ("stale input",),
                            NOW.date() - timedelta(days=1), NOW.date() + timedelta(days=1), "forecast-owner")
    registry.register(model)
    for index, status in enumerate((ModelStatus.VALIDATED, ModelStatus.APPROVED, ModelStatus.ACTIVE)):
        registry.transition(model.key, status, effective_at=NOW - timedelta(minutes=3 - index),
                            reason="review", evidence_ids=(f"economic-review-{index}",))
    clock = ReplayClock(NOW - timedelta(minutes=5))
    evidence = RuntimeModelRegistryEvidenceRepository(conn, clock)
    for transition in registry._transitions:
        for evidence_id in transition.evidence_ids:
            evidence.record_review_evidence(evidence_id, model_key=transition.model_key,
                                            from_status=transition.from_status, to_status=transition.to_status,
                                            owner="forecast-owner", evidence={"review": "approved"})
    clock.advance_to(NOW - timedelta(seconds=1))
    snapshot = evidence.publish_snapshot(registry)
    clock.advance_to(NOW)
    evidence.bind_runtime_run("run@1", snapshot)
    return AsOfRepository(conn), evidence, evidence.authorize(
        "run@1", model_key="economic@1", scope=ModelScope.EXPECTED_RETURN)


def assemble(source):
    inputs, evidence, authorization = source
    return ComponentForecastAssembler(inputs, evidence).assemble(
        context=CONTEXT, instrument_id="SPY", asset_class=AssetClass.EQUITY_ETF,
        currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252, validity=VALIDITY,
        model_key="economic@1", runtime_authorization=authorization,
    )


def test_assembler_reads_persisted_pit_values_replays_and_feeds_typed_consumer():
    source = repository()
    first = assemble(source)
    second = assemble(source)
    assert first == second
    assert len(first.components) == 5
    assert all(item.input_features[0].startswith("observation:") for item in first.components)
    assert first.lineage_id == second.lineage_id
    assert all(item.dataset_manifest_id == MANIFEST for item in first.lineage)
    inputs, evidence, authorization = source
    gross = ComponentForecastAssembler(inputs, evidence).assemble_gross_forecast(
        context=CONTEXT, instrument_id="SPY", asset_class=AssetClass.EQUITY_ETF,
        currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252, validity=VALIDITY,
        model_key="economic@1", runtime_authorization=authorization,
    )
    assert gross.gross_expected_return == sum(item.point_estimate for item in first.components)
    assert not hasattr(gross, "net_expected_return")


@pytest.mark.parametrize("source", [
    lambda: repository(runtime="other-run"),
    lambda: repository(fresh_until=NOW),
    lambda: repository(conflicting=True),
    lambda: repository(manifest_runtime="other-run"),
    lambda: repository(payload_cutoff=NOW - timedelta(seconds=1)),
    lambda: repository(duplicate_exposure=True),
])
def test_cross_run_stale_or_conflicting_persisted_evidence_fails_closed(source):
    with pytest.raises(DataQualityError):
        assemble(source())


@pytest.mark.parametrize("semantic_type", ["PRICING_BASELINE_RETURN", "MODEL_RELATIVE_ALPHA", "FORECAST_TOTAL_RETURN_GROSS"])
def test_pricing_or_alpha_semantics_cannot_be_substituted_for_economic_input(semantic_type):
    with pytest.raises(DataQualityError, match="ECONOMIC_INPUT_CONTEXT_CONFLICT"):
        assemble(repository(semantic_type=semantic_type))


def test_caller_cannot_supply_a_fake_repository_or_economic_values():
    with pytest.raises(DataQualityError, match="COMPONENT_FORECAST_REPOSITORY_REQUIRED"):
        ComponentForecastAssembler(object(), object())


def test_downstream_consumer_accepts_no_caller_authored_bundle():
    inputs, evidence, authorization = repository()
    assembler = ComponentForecastAssembler(inputs, evidence)
    with pytest.raises(TypeError):
        assembler.assemble_gross_forecast(bundle=assemble((inputs, evidence, authorization)))
    with pytest.raises(DataQualityError, match="DIRECT_EXPECTED_RETURN_ASSEMBLY_RETIRED"):
        expected_return(instrument_id="SPY", asset_class=AssetClass.EQUITY_ETF, components={},
                        horizon=252, as_of=NOW)


def test_unapproved_model_key_cannot_select_persisted_economic_values():
    inputs, evidence, authorization = repository()
    with pytest.raises(DataQualityError, match="COMPONENT_FORECAST_MODEL_AUTHORIZATION_INVALID"):
        ComponentForecastAssembler(inputs, evidence).assemble(
            context=CONTEXT, instrument_id="SPY", asset_class=AssetClass.EQUITY_ETF,
            currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252, validity=VALIDITY,
            model_key="unapproved@1", runtime_authorization=authorization,
        )


def test_caller_cannot_widen_the_persisted_runtime_cutoff():
    inputs, evidence, authorization = repository()
    widened = AsOfContext("run@1", NOW + timedelta(seconds=1), NOW + timedelta(seconds=1),
                          "policy@1", "parameters@1", "git:test")
    with pytest.raises(DataQualityError, match="RUNTIME_CONTEXT_CONFLICT"):
        ComponentForecastAssembler(inputs, evidence).assemble(
            context=widened, instrument_id="SPY", asset_class=AssetClass.EQUITY_ETF,
            currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252, validity=VALIDITY,
            model_key="economic@1", runtime_authorization=authorization,
        )


def test_persisted_calculation_replays_by_id_and_rejects_cross_run_or_missing_lineage():
    inputs, evidence, authorization = repository()
    assembler = ComponentForecastAssembler(inputs, evidence)
    persisted = assembler.assemble_gross_forecast(
        context=CONTEXT, instrument_id="SPY", asset_class=AssetClass.EQUITY_ETF,
        currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252, validity=VALIDITY,
        model_key="economic@1", runtime_authorization=authorization,
    )
    assert assembler.replay_gross_forecast(calculation_id=persisted.calculation_id) == persisted
    with pytest.raises(DataQualityError):
        assembler.replay_gross_forecast(calculation_id="b" * 64)


def persisted_calculation():
    inputs, evidence, authorization = repository()
    assembler = ComponentForecastAssembler(inputs, evidence)
    persisted = assembler.assemble_gross_forecast(
        context=CONTEXT, instrument_id="SPY", asset_class=AssetClass.EQUITY_ETF,
        currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252, validity=VALIDITY,
        model_key="economic@1", runtime_authorization=authorization,
    )
    row = inputs.connection.execute(
        "SELECT canonical_artifact_json FROM am_component_forecast_calculation WHERE component_forecast_calculation_id=?",
        (persisted.calculation_id,),).fetchone()
    artifact = json.loads(row[0])
    assert persisted.calculation_id == digest(canonical(artifact))
    assert assembler.consume_gross_forecast(calculation_id=persisted.calculation_id) == persisted
    return inputs, assembler, persisted, artifact


def insert_forged(inputs, artifact, *, runtime="run@1"):
    calculation_id = digest(canonical(artifact))
    inputs.connection.execute(
        "INSERT INTO am_component_forecast_calculation VALUES (?, ?, ?, ?, ?)",
        (calculation_id, runtime, json.dumps(artifact, sort_keys=True, separators=(",", ":")),
         calculation_id, artifact["information_cutoff_utc"]),
    )
    return calculation_id


def test_partial_persisted_bundle_mutation_fails_closed():
    inputs, assembler, persisted, artifact = persisted_calculation()
    artifact["gross_forecast"]["gross_expected_return"] = "99"
    inputs.connection.execute("DROP TRIGGER am_component_forecast_calculation_no_update")
    inputs.connection.execute(
        "UPDATE am_component_forecast_calculation SET canonical_artifact_json=? WHERE component_forecast_calculation_id=?",
        (json.dumps(artifact, sort_keys=True, separators=(",", ":")), persisted.calculation_id),)
    with pytest.raises(DataQualityError, match="COMPONENT_FORECAST_LINEAGE_UNVERIFIED"):
        assembler.replay_gross_forecast(calculation_id=persisted.calculation_id)


@pytest.mark.parametrize("mutation", ["forged_gross", "reused_observation", "missing_lineage",
                                      "model_binding", "semantic_drift", "cutoff_mismatch"])
def test_rehashed_forged_calculation_rows_fail_closed(mutation):
    inputs, assembler, _, original = persisted_calculation()
    artifact = deepcopy(original)
    if mutation == "forged_gross":
        artifact["gross_forecast"]["gross_expected_return"] = "99"
    elif mutation == "reused_observation":
        artifact["observation_lineage"][0] = deepcopy(artifact["observation_lineage"][1])
        artifact["observation_lineage"][0]["component_type"] = artifact["components"][0]["component_type"]
    elif mutation == "missing_lineage":
        artifact["observation_lineage"].pop()
    elif mutation == "model_binding":
        artifact["model_authorization"]["binding_hash"] = "b" * 64
    elif mutation == "semantic_drift":
        artifact["semantic_contract"] = "PRICING_BASELINE_RETURN"
    else:
        artifact["information_cutoff_utc"] = (NOW - timedelta(seconds=1)).isoformat()
    forged_id = insert_forged(inputs, artifact)
    with pytest.raises(DataQualityError):
        assembler.replay_gross_forecast(calculation_id=forged_id)


def test_cross_run_forged_reference_fails_closed():
    inputs, assembler, _, original = persisted_calculation()
    inputs.connection.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
        ("other-run", NOW.isoformat(), NOW.isoformat(), "git:other", NOW.isoformat()))
    artifact = deepcopy(original); artifact["runtime_run_id"] = "other-run"
    forged_id = insert_forged(inputs, artifact, runtime="other-run")
    with pytest.raises(DataQualityError):
        assembler.replay_gross_forecast(calculation_id=forged_id)


def test_mutated_observation_hash_fails_closed_during_replay():
    inputs, assembler, persisted, artifact = persisted_calculation()
    observation_id = artifact["observation_lineage"][0]["observation_id"]
    inputs.connection.execute("DROP TRIGGER am_temporal_observation_update_block")
    inputs.connection.execute("UPDATE am_temporal_observation SET content_hash=? WHERE observation_id=?",
                              ("b" * 64, observation_id))
    with pytest.raises(DataQualityError, match="COMPONENT_FORECAST_LINEAGE_UNVERIFIED"):
        assembler.replay_gross_forecast(calculation_id=persisted.calculation_id)


def test_forged_calculation_referencing_stale_observation_fails_closed():
    inputs, assembler, _, original = persisted_calculation()
    source = original["observation_lineage"][0]
    prior = inputs.get_by_id(source["observation_id"])
    instant = NOW - timedelta(days=1)
    stale = SQLiteTemporalObservationStore(inputs.connection).append(
        entity_id="SPY", field=prior.field,
        value=prior.value | {"fresh_until_utc": NOW.isoformat()},
        reference_period=instant.date().isoformat(), event_time=instant,
        scheduled_release_at=None, official_release_at=instant, source_timestamp=instant,
        received_at=instant, available_at=instant, ingested_at=instant, revised_at=None,
        source_timezone="UTC", schema_version=prior.schema_version,
        dataset_manifest_id=prior.dataset_manifest_id,
    )
    artifact = deepcopy(original)
    artifact["observation_lineage"][0] |= {
        "observation_id": stale.observation_id, "observation_hash": stale.content_hash,
        "available_at_utc": stale.available_at.isoformat(),
    }
    forged_id = insert_forged(inputs, artifact)
    with pytest.raises(DataQualityError, match="COMPONENT_FORECAST_(LINEAGE_UNVERIFIED|SEMANTIC_CONTRACT_INVALID)"):
        assembler.replay_gross_forecast(calculation_id=forged_id)


def test_forged_calculation_reusing_noncanonical_valid_observation_fails_closed():
    inputs, assembler, _, original = persisted_calculation()
    source = original["observation_lineage"][0]
    selected = inputs.get_by_id(source["observation_id"])
    instant = NOW - timedelta(days=1)
    older = SQLiteTemporalObservationStore(inputs.connection).append(
        entity_id="SPY", field=selected.field,
        value=selected.value | {"annualized_return": "0.50"},
        reference_period=instant.date().isoformat(), event_time=instant,
        scheduled_release_at=None, official_release_at=instant, source_timestamp=instant,
        received_at=instant, available_at=instant, ingested_at=instant, revised_at=None,
        source_timezone="UTC", schema_version=selected.schema_version,
        dataset_manifest_id=selected.dataset_manifest_id,
    )
    artifact = deepcopy(original)
    artifact["observation_lineage"][0] |= {
        "observation_id": older.observation_id, "observation_hash": older.content_hash,
        "available_at_utc": older.available_at.isoformat(), "annualized_return": "0.50",
    }
    artifact["components"][0] |= {
        "point_estimate": "0.404",
        "input_features": [f"observation:{older.content_hash}"],
    }
    artifact["gross_forecast"] |= {
        "gross_expected_return": "0.740",
        "lower_bound": "0.4460",
        "upper_bound": "1.0340",
    }
    forged_id = insert_forged(inputs, artifact)
    with pytest.raises(DataQualityError, match="COMPONENT_FORECAST_LINEAGE_UNVERIFIED"):
        assembler.replay_gross_forecast(calculation_id=forged_id)


def test_rehashed_noncanonical_json_type_drift_fails_closed():
    inputs, assembler, _, original = persisted_calculation()
    artifact = deepcopy(original)
    artifact["horizon"] = "252"
    forged_id = insert_forged(inputs, artifact)
    with pytest.raises(DataQualityError, match="COMPONENT_FORECAST_SEMANTIC_CONTRACT_INVALID"):
        assembler.replay_gross_forecast(calculation_id=forged_id)


def test_rehashed_observation_id_type_drift_fails_closed():
    inputs, evidence, authorization = repository(numeric_observation_ids=True)
    assembler = ComponentForecastAssembler(inputs, evidence)
    persisted = assembler.assemble_gross_forecast(
        context=CONTEXT, instrument_id="SPY", asset_class=AssetClass.EQUITY_ETF,
        currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252,
        validity=VALIDITY, model_key="economic@1", runtime_authorization=authorization,
    )
    row = inputs.connection.execute(
        "SELECT canonical_artifact_json FROM am_component_forecast_calculation "
        "WHERE component_forecast_calculation_id=?", (persisted.calculation_id,),
    ).fetchone()
    artifact = json.loads(row[0])
    original_id = artifact["observation_lineage"][0]["observation_id"]
    artifact["observation_lineage"][0]["observation_id"] = int(original_id)
    forged_id = insert_forged(inputs, artifact)
    with pytest.raises(DataQualityError, match="COMPONENT_FORECAST_LINEAGE_UNVERIFIED"):
        assembler.replay_gross_forecast(calculation_id=forged_id)


def test_physical_commodity_excludes_roll_yield_and_rejects_unsupported_structure():
    source, evidence, authorization = repository()
    assembler = ComponentForecastAssembler(source, evidence)
    result = assembler.assemble(
        context=CONTEXT, instrument_id="SPY", asset_class=AssetClass.COMMODITY_ETF,
        currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252, validity=VALIDITY,
        model_key="economic@1", runtime_authorization=authorization,
        commodity_structure=CommodityStructure.PHYSICAL_BACKED,
    )
    assert {item.component_name for item in result.components} == {"spot_change", "carry", "expense"}
    gross = assembler.assemble_gross_forecast(
        context=CONTEXT, instrument_id="SPY", asset_class=AssetClass.COMMODITY_ETF,
        currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252, validity=VALIDITY,
        model_key="economic@1", runtime_authorization=authorization,
        commodity_structure=CommodityStructure.PHYSICAL_BACKED,
    )
    assert gross.gross_expected_return == sum(item.point_estimate for item in result.components)
    with pytest.raises(DataQualityError, match="COMMODITY_STRUCTURE_UNSUPPORTED"):
        assembler.assemble(
            context=CONTEXT, instrument_id="SPY", asset_class=AssetClass.COMMODITY_ETF,
            currency=Currency.USD, currency_basis=CurrencyBasis.BASE, horizon=252, validity=VALIDITY,
            model_key="economic@1", runtime_authorization=authorization,
            commodity_structure=CommodityStructure.OTHER,
        )
