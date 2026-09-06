from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.features.models import FeatureSnapshot
from asset_management.quality.models import QualityGate, QualityStatus
from asset_management.signals import (
    CostSensitivity, SignalContext, SignalDefinition, SignalDirectionality,
    SignalFeatureInput, SignalRegistry, SignalStore, SignalType,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
CUTOFF = NOW
LICENSE = "purpose=internal-research;redistribution=forbidden;retention=perpetual"
VALIDITY = SignalValidity(63, 21, NOW + timedelta(days=1), DecayProfile.LINEAR)


def definition(**changes):
    base = dict(
        signal_id="value.relative_strength", version="1", name="relative strength",
        economic_rationale="Relative price strength can summarize persistent investor demand.",
        source_feature_ids=("market.return_1m",), transform_rule="rank_transform",
        ranking_rule="PIT_ORDINAL", normalization_rule="NONE", neutralization_rule="NONE",
        directionality=SignalDirectionality.POSITIVE, signal_type=SignalType.CROSS_SECTIONAL,
        validity=VALIDITY, universe_id="etf-us-v1", currency="USD",
        minimum_coverage=Decimal("0.5"), minimum_history=2,
        missing_policy="ABSTAIN_BELOW_COVERAGE", outlier_policy="PIT_WINSORIZE",
        expected_turnover=Decimal("0.2"), cost_sensitivity=CostSensitivity.MEDIUM,
        formula_version="relative-strength@1", parameter_set_id="signal-parameters-v1",
    )
    base.update(changes)
    return SignalDefinition(**base)


def allow_gate():
    return QualityGate("ALLOW", (), QualityStatus.VALID, QualityStatus.VALID, "HIGH")


def seed_store(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    raw = store.write(
        {"items": [{"instrument_id": "AAA"}, {"instrument_id": "BBB"}]},
        layer="bronze", source="provider", dataset="universe-raw", schema_version="raw-v1",
        retrieved_at=NOW - timedelta(minutes=4), available_at=NOW - timedelta(minutes=2),
        provider_timestamp=NOW - timedelta(minutes=5), license_tag=LICENSE,
        code_revision="git:abcdef0", request_hash="a" * 64, quality_status="RAW",
    )
    universe = store.write(
        [{"instrument_id": "AAA"}, {"instrument_id": "BBB"}],
        layer="silver", source="provider", dataset="historical-universe", schema_version="universe-v1",
        retrieved_at=NOW - timedelta(minutes=4), available_at=NOW - timedelta(minutes=1),
        provider_timestamp=NOW - timedelta(minutes=5), license_tag=LICENSE,
        code_revision="git:abcdef0", request_hash="b" * 64,
        parent_manifest_ids=(raw.manifest_id,),
    )
    price_raw = store.write(
        {"items": [{"instrument_id": "AAA", "close": "100"}]},
        layer="bronze", source="provider", dataset="prices-raw", schema_version="raw-v1",
        retrieved_at=NOW - timedelta(minutes=4), available_at=NOW - timedelta(minutes=2),
        provider_timestamp=NOW - timedelta(minutes=5), license_tag=LICENSE,
        code_revision="git:abcdef0", request_hash="c" * 64, quality_status="RAW",
    )
    prices = store.write(
        [{"instrument_id": "AAA", "close": "100"}],
        layer="silver", source="provider", dataset="prices", schema_version="prices-v1",
        retrieved_at=NOW - timedelta(minutes=4), available_at=NOW - timedelta(minutes=1),
        provider_timestamp=NOW - timedelta(minutes=5), license_tag=LICENSE,
        code_revision="git:abcdef0", request_hash="d" * 64,
        parent_manifest_ids=(price_raw.manifest_id,),
    )
    return store, universe.manifest_id, prices.manifest_id


def feature_snapshot(instrument, value, universe_manifest_id, price_manifest_id, *, quality="VALID"):
    run_id = digest(canonical({"instrument": instrument, "value": value, "quality": quality}))
    return FeatureSnapshot(
        run_id, instrument, "market.return_1m", NOW.isoformat(), CUTOFF.isoformat(), value,
        quality, (price_manifest_id, universe_manifest_id), "feature-parameters-v1", None,
        "git:abcdef0", VALIDITY,
    )


def publish_feature(store, snapshot):
    body = {
        "feature_run_id": snapshot.feature_run_id, "instrument_id": snapshot.instrument_id,
        "feature_id": snapshot.feature_id, "as_of": snapshot.as_of,
        "information_cutoff": snapshot.information_cutoff, "value": snapshot.value,
        "quality_status": snapshot.quality_status,
        "input_manifest_ids": list(snapshot.input_manifest_ids),
        "parameter_set_id": snapshot.parameter_set_id, "parent_state_id": snapshot.parent_state_id,
        "code_revision": snapshot.code_revision, "validity": snapshot.validity.payload(),
    }
    return store.write(
        body, layer="gold", source="provider", dataset="feature-snapshot",
        schema_version="phase11-feature-snapshot-v1", retrieved_at=NOW, available_at=NOW,
        provider_timestamp=NOW - timedelta(minutes=3), license_tag=LICENSE,
        code_revision="git:abcdef0", request_hash=digest(canonical(body)),
        parent_manifest_ids=snapshot.input_manifest_ids,
    ).manifest_id


def prepared(tmp_path, *, second_value="0.2", second_quality="VALID"):
    store, universe_id, price_id = seed_store(tmp_path)
    first = feature_snapshot("AAA", "0.1", universe_id, price_id)
    second = feature_snapshot("BBB", second_value, universe_id, price_id, quality=second_quality)
    first_manifest = publish_feature(store, first)
    second_manifest = publish_feature(store, second)
    history_a = publish_feature(store, feature_snapshot("AAA", "0.05", universe_id, price_id))
    history_b = publish_feature(store, feature_snapshot("BBB", "0.06", universe_id, price_id))
    context = SignalContext(NOW, CUTOFF, universe_id, ("AAA", "BBB"),
                            (history_a, history_b), "git:abcdef0")
    inputs = {
        "AAA": {"market.return_1m": SignalFeatureInput(first, first_manifest)},
        "BBB": {"market.return_1m": SignalFeatureInput(second, second_manifest)},
    }
    return store, context, inputs


def rank_transform(values):
    ordered = sorted(values, key=lambda item: (values[item]["market.return_1m"], item))
    return {instrument: Decimal(index + 1) / Decimal(len(ordered))
            for index, instrument in enumerate(ordered)}


def test_signal_contract_is_complete_versioned_and_separate_from_features(tmp_path):
    registry = SignalRegistry([definition()])
    registry.register(definition())
    registry.register(definition(version="2", formula_version="relative-strength@2"))
    assert registry.get("value.relative_strength", "1") == definition()
    assert set(registry.payload()) == {"definitions", "registry_hash"}
    with pytest.raises(InvariantViolation, match="SIGNAL_DEFINITION_CONFLICT"):
        registry.register(replace(definition(), economic_rationale="different"))
    store, _, _ = seed_store(tmp_path)
    catalog = registry.publish(store)
    assert len(catalog) == 64
    assert "feature_id" not in definition().payload()


@pytest.mark.parametrize("changes,reason", [
    ({"signal_id": "BUY"}, "SIGNAL_DEFINITION_INVALID"),
    ({"source_feature_ids": ()}, "SIGNAL_SOURCE_FEATURES_INVALID"),
    ({"currency": "US"}, "SIGNAL_DEFINITION_INVALID"),
    ({"minimum_coverage": Decimal("0")}, "SIGNAL_DEFINITION_INVALID"),
    ({"minimum_history": 0}, "SIGNAL_DEFINITION_INVALID"),
    ({"expected_turnover": Decimal("1.1")}, "SIGNAL_DEFINITION_INVALID"),
])
def test_incomplete_signal_definition_fails_closed(changes, reason):
    with pytest.raises(InvariantViolation, match=reason):
        definition(**changes)


def test_signal_uses_only_pit_feature_snapshots_and_publishes_separate_value(tmp_path):
    store, context, inputs = prepared(tmp_path)
    result = SignalStore(store, SignalRegistry([definition()])).evaluate(
        signal_id="value.relative_strength", version="1", context=context,
        feature_inputs=inputs, transform=rank_transform, quality_gate=allow_gate(),
    )
    assert result.status == "READY" and result.reason_code == "OK" and result.catalog_id
    assert result.snapshot is not None
    assert result.snapshot.semantic_type == "SIGNAL_VALUE"
    assert result.snapshot.values == {"AAA": "0.5", "BBB": "1"}
    assert result.snapshot.quality_status == "VALID" and len(result.snapshot.output_hash) == 64
    stored = json.loads((tmp_path / "catalog" / "signal-snapshots" /
                         f"{result.catalog_id}.json").read_text())
    assert stored == result.snapshot.payload()
    assert not ({"BUY", "SELL", "target_weight"} & set(result.snapshot.payload()))


def test_missing_coverage_returns_abstain_without_publishing_signal(tmp_path):
    store, context, inputs = prepared(tmp_path, second_value=None, second_quality="MISSING")
    result = SignalStore(store, SignalRegistry([definition(minimum_coverage=Decimal("1"))])).evaluate(
        signal_id="value.relative_strength", version="1", context=context,
        feature_inputs=inputs, transform=rank_transform, quality_gate=allow_gate(),
    )
    assert (result.status, result.reason_code, result.snapshot, result.catalog_id) == (
        "ABSTAIN", "SIGNAL_COVERAGE_INSUFFICIENT", None, None)


def test_signal_snapshot_hash_and_feature_manifest_lineage_cannot_be_fabricated(tmp_path):
    store, context, inputs = prepared(tmp_path)
    signal_store = SignalStore(store, SignalRegistry([definition()]))
    result = signal_store.evaluate(
        signal_id="value.relative_strength", version="1", context=context,
        feature_inputs=inputs, transform=rank_transform, quality_gate=allow_gate(),
    )
    assert result.snapshot is not None
    with pytest.raises(InvariantViolation, match="SIGNAL_SNAPSHOT_INVALID"):
        replace(result.snapshot, output_hash="0" * 64)
    bad_inputs = dict(inputs) | {"AAA": {"market.return_1m": SignalFeatureInput(
        inputs["AAA"]["market.return_1m"].snapshot, "0" * 64)}}
    assert signal_store.evaluate(
        signal_id="value.relative_strength", version="1", context=context,
        feature_inputs=bad_inputs, transform=rank_transform, quality_gate=allow_gate(),
    ).reason_code == "SIGNAL_FEATURE_MANIFEST_UNVERIFIED"


def test_current_universe_future_feature_transform_and_history_gaps_fail_closed(tmp_path):
    store, context, inputs = prepared(tmp_path)
    signal_store = SignalStore(store, SignalRegistry([definition()]))
    bad_universe = dict(inputs) | {"CCC": inputs["AAA"]}
    assert signal_store.evaluate(
        signal_id="value.relative_strength", version="1", context=context,
        feature_inputs=bad_universe, transform=rank_transform, quality_gate=allow_gate(),
    ).reason_code == "SIGNAL_UNIVERSE_PIT_INVALID"
    assert signal_store.evaluate(
        signal_id="value.relative_strength", version="1", context=context,
        feature_inputs=inputs, transform=lambda values: {}, quality_gate=allow_gate(),
    ).reason_code == "SIGNAL_TRANSFORMATION_MISMATCH"
    short_history = replace(context, history_feature_manifest_ids=(context.history_feature_manifest_ids[0],))
    assert signal_store.evaluate(
        signal_id="value.relative_strength", version="1", context=short_history,
        feature_inputs=inputs, transform=rank_transform, quality_gate=allow_gate(),
    ).reason_code == "SIGNAL_MINIMUM_HISTORY_MISSING"
    future = replace(inputs["AAA"]["market.return_1m"].snapshot,
                     information_cutoff=(NOW + timedelta(seconds=1)).isoformat())
    future_inputs = dict(inputs) | {"AAA": {"market.return_1m": SignalFeatureInput(
        future, inputs["AAA"]["market.return_1m"].manifest_id)}}
    assert signal_store.evaluate(
        signal_id="value.relative_strength", version="1", context=context,
        feature_inputs=future_inputs, transform=rank_transform, quality_gate=allow_gate(),
    ).reason_code == "SIGNAL_FEATURE_CONTEXT_INVALID"


def test_signal_schema_matches_definition_and_snapshot_contract(tmp_path):
    store, context, inputs = prepared(tmp_path)
    result = SignalStore(store, SignalRegistry([definition()])).evaluate(
        signal_id="value.relative_strength", version="1", context=context,
        feature_inputs=inputs, transform=rank_transform, quality_gate=allow_gate(),
    )
    root = __import__("pathlib").Path(__file__).parents[1]
    definition_schema = json.loads((root / "schemas/signal_definition.schema.json").read_text())
    snapshot_schema = json.loads((root / "schemas/signal_snapshot.schema.json").read_text())
    assert set(definition_schema["required"]) == set(definition().payload())
    assert result.snapshot is not None
    assert set(snapshot_schema["required"]) == set(result.snapshot.payload())
