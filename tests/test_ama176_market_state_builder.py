from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.features.models import FeatureSnapshot
from asset_management.features.registry import FeatureRegistry, builtin_definitions
from asset_management.features.store import identity_for_request
from asset_management.quality.models import QualityStatus
from asset_management.states import (
    MarketStateBuilder,
    MarketStateComponentSpec,
    MarketStateSpec,
    OperationalState,
    StateFeatureInput,
    StateNormalization,
    StatePolicy,
    foundation_market_state_spec,
)
from asset_management.states.market import MARKET_COMPONENTS


NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(minutes=1)
FEATURE_AS_OF = NOW - timedelta(minutes=2)
FEATURE_CUTOFF = FEATURE_AS_OF
VALIDITY = SignalValidity(21, 21, NOW + timedelta(days=30), DecayProfile.STEP)
POLICY = StatePolicy("state-policy-v1", Decimal("0.8"), Decimal("0.5"), 300)
LICENSE = "purpose=research;redistribution=forbidden;retention=test"
CODE = "git:abcdef0"


def registry() -> FeatureRegistry:
    return FeatureRegistry(builtin_definitions())


def direct_breadth_spec() -> MarketStateSpec:
    items = []
    for name in MARKET_COMPONENTS:
        if name == "breadth":
            items.append(MarketStateComponentSpec(
                component_id="breadth",
                semantic_type="BREADTH_STATE",
                unit="fraction",
                formula_version="breadth.identity@1",
                source_feature_id="market.breadth",
                source_instrument_id="MARKET",
                normalization=StateNormalization.RAW,
            ))
        else:
            items.append(MarketStateComponentSpec(
                component_id=name,
                semantic_type=f"{name.upper()}_STATE",
                unit="unknown",
                formula_version=f"{name}.unavailable@1",
            ))
    return MarketStateSpec("test-direct-breadth", "1", tuple(items))


def _write_bronze(store: ImmutableDatasetStore, *, dataset: str, stamp: datetime):
    return store.write(
        {"rows": [{"value": dataset}]},
        layer="bronze", source="fixture", dataset=f"raw-{dataset}", schema_version="raw@1",
        retrieved_at=stamp, available_at=stamp, provider_timestamp=stamp,
        license_tag=LICENSE, code_revision=CODE,
        request_hash=digest(canonical({"raw": dataset})),
        quality_status="RAW",
    )


def _write_silver(store: ImmutableDatasetStore, *, dataset: str, stamp: datetime):
    bronze = _write_bronze(store, dataset=dataset, stamp=stamp)
    return store.write(
        {"rows": [{"value": dataset}]},
        layer="silver", source="fixture", dataset=dataset, schema_version="silver@1",
        retrieved_at=stamp, available_at=stamp, provider_timestamp=stamp,
        license_tag=LICENSE, code_revision=CODE,
        request_hash=digest(canonical({"silver": dataset})),
        parent_manifest_ids=(bronze.manifest_id,),
    )


def published_feature(
    store: ImmutableDatasetStore,
    feature_registry: FeatureRegistry,
    *,
    value: str = "0.6",
    gold_available_at: datetime = FEATURE_AS_OF,
    use_bronze_market_parent: bool = False,
    request_hash_override: str | None = None,
) -> StateFeatureInput:
    source_stamp = FEATURE_AS_OF - timedelta(minutes=1)
    universe = _write_silver(store, dataset="historical-universe", stamp=source_stamp)
    prices = (
        _write_bronze(store, dataset="market-bars", stamp=source_stamp)
        if use_bronze_market_parent
        else _write_silver(store, dataset="market-bars", stamp=source_stamp)
    )
    definition = feature_registry.get("market.breadth")
    definition_id = store.catalog("feature-definitions", asdict(definition))
    parents = tuple(sorted((universe.manifest_id, prices.manifest_id)))
    identity = {
        "definition": asdict(definition),
        "instrument_id": "MARKET",
        "as_of": FEATURE_AS_OF.isoformat(),
        "information_cutoff": FEATURE_CUTOFF.isoformat(),
        "value": value,
        "parents": list(parents),
    }
    snapshot = FeatureSnapshot(
        feature_run_id=digest(canonical(identity)),
        instrument_id="MARKET",
        feature_id="market.breadth",
        as_of=FEATURE_AS_OF.isoformat(),
        information_cutoff=FEATURE_CUTOFF.isoformat(),
        value=value,
        quality_status=QualityStatus.VALID.value,
        input_manifest_ids=parents,
        parameter_set_id="breadth-feature-params@1",
        parent_state_id=None,
        code_revision=CODE,
        validity=VALIDITY,
    )
    body = {
        **asdict(snapshot),
        "input_manifest_ids": list(snapshot.input_manifest_ids),
        "validity": snapshot.validity.payload(),
        "feature_definition_catalog_id": definition_id,
    }
    expected_request_hash = digest(canonical(identity_for_request(snapshot, definition_id)))
    gold = store.write(
        body,
        layer="gold", source="fixture", dataset="feature-snapshot",
        schema_version="phase11-feature-snapshot-v1",
        retrieved_at=gold_available_at, available_at=gold_available_at,
        provider_timestamp=source_stamp,
        license_tag=LICENSE, code_revision=CODE,
        request_hash=request_hash_override or expected_request_hash,
        parent_manifest_ids=parents,
    )
    return StateFeatureInput(snapshot, gold.manifest_id)


def test_foundation_spec_preserves_all_nine_dimensions_as_explicit_unavailable(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    feature_registry = registry()
    market_builder = MarketStateBuilder(store, feature_registry)
    spec = foundation_market_state_spec()

    first = market_builder.build(
        spec=spec, as_of=NOW, information_cutoff=CUTOFF,
        policy=POLICY, code_revision=CODE,
    )
    second = market_builder.build(
        spec=spec, as_of=NOW, information_cutoff=CUTOFF,
        policy=POLICY, code_revision=CODE,
    )

    assert first.spec_catalog_id == spec.spec_hash == second.spec_catalog_id
    assert first.snapshot.state_id == second.snapshot.state_id
    assert tuple(first.snapshot.components) == MARKET_COMPONENTS
    assert first.snapshot.input_feature_ids == ()
    assert first.snapshot.quality_status is QualityStatus.MISSING
    assert first.snapshot.operational_state is OperationalState.NO_NEW_TRADES
    assert first.snapshot.risk_multiplier == "0"
    for component in first.snapshot.components.values():
        assert component.value is None
        assert component.confidence == Decimal(0)
        assert component.reason_code == "UNMAPPED_COMPONENT"
        assert component.input_features == ()
        assert component.input_evidence_ids == (spec.spec_hash,)


def test_direct_binding_reverifies_published_feature_and_keeps_other_dimensions_unavailable(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    feature_registry = registry()
    market_builder = MarketStateBuilder(store, feature_registry)
    item = published_feature(store, feature_registry)
    spec = direct_breadth_spec()

    result = market_builder.build(
        spec=spec, as_of=NOW, information_cutoff=CUTOFF,
        policy=POLICY, code_revision=CODE,
        feature_inputs={"breadth": item},
    )

    breadth = result.snapshot.components["breadth"]
    assert breadth.value == Decimal("0.6")
    assert breadth.unit == "fraction"
    assert breadth.normalization is StateNormalization.RAW
    assert breadth.confidence == Decimal(1)
    assert breadth.input_features == (item,)
    assert item.manifest_id in breadth.input_evidence_ids
    assert result.spec_catalog_id in breadth.input_evidence_ids
    assert result.snapshot.input_feature_ids == ("market.breadth",)
    assert set(result.snapshot.input_data_manifest_ids) == set(item.snapshot.input_manifest_ids)
    assert result.snapshot.operational_state is OperationalState.NO_NEW_TRADES
    assert result.snapshot.components["growth"].reason_code == "UNMAPPED_COMPONENT"


def test_missing_approved_feature_becomes_explicit_unavailable_not_a_proxy(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    market_builder = MarketStateBuilder(store, registry())
    result = market_builder.build(
        spec=direct_breadth_spec(), as_of=NOW, information_cutoff=CUTOFF,
        policy=POLICY, code_revision=CODE,
    )
    breadth = result.snapshot.components["breadth"]
    assert breadth.value is None
    assert breadth.reason_code == "SOURCE_FEATURE_UNAVAILABLE"
    assert breadth.confidence == Decimal(0)
    assert breadth.input_features == ()


def test_unbound_feature_input_is_rejected_instead_of_implicitly_promoted(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    feature_registry = registry()
    item = published_feature(store, feature_registry)
    market_builder = MarketStateBuilder(store, feature_registry)
    with pytest.raises(DataQualityError, match="MARKET_STATE_UNBOUND_FEATURE_INPUT"):
        market_builder.build(
            spec=foundation_market_state_spec(), as_of=NOW, information_cutoff=CUTOFF,
            policy=POLICY, code_revision=CODE, feature_inputs={"breadth": item},
        )


def test_tampered_snapshot_cannot_reuse_real_feature_manifest(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    feature_registry = registry()
    item = published_feature(store, feature_registry)
    tampered = StateFeatureInput(replace(item.snapshot, value="0.9"), item.manifest_id)
    market_builder = MarketStateBuilder(store, feature_registry)
    with pytest.raises(DataQualityError, match="MARKET_STATE_FEATURE_MANIFEST_INVALID"):
        market_builder.build(
            spec=direct_breadth_spec(), as_of=NOW, information_cutoff=CUTOFF,
            policy=POLICY, code_revision=CODE, feature_inputs={"breadth": tampered},
        )


def test_feature_identity_and_manifest_availability_fail_closed(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    feature_registry = registry()
    item = published_feature(store, feature_registry)
    wrong_identity = StateFeatureInput(replace(item.snapshot, instrument_id="SPY"), item.manifest_id)
    market_builder = MarketStateBuilder(store, feature_registry)
    with pytest.raises(DataQualityError, match="MARKET_STATE_FEATURE_IDENTITY_INVALID"):
        market_builder.build(
            spec=direct_breadth_spec(), as_of=NOW, information_cutoff=CUTOFF,
            policy=POLICY, code_revision=CODE, feature_inputs={"breadth": wrong_identity},
        )

    late_item = published_feature(
        store, feature_registry, value="0.7", gold_available_at=NOW + timedelta(minutes=1))
    with pytest.raises(DataQualityError, match="MARKET_STATE_FEATURE_MANIFEST_INVALID"):
        market_builder.build(
            spec=direct_breadth_spec(), as_of=NOW + timedelta(minutes=2), information_cutoff=CUTOFF,
            policy=POLICY, code_revision=CODE, feature_inputs={"breadth": late_item},
        )


def test_forged_featurestore_publication_contract_is_rejected(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    feature_registry = registry()
    market_builder = MarketStateBuilder(store, feature_registry)

    early_publication = published_feature(
        store,
        feature_registry,
        value="0.61",
        gold_available_at=FEATURE_AS_OF - timedelta(seconds=30),
    )
    with pytest.raises(DataQualityError, match="MARKET_STATE_FEATURE_MANIFEST_INVALID"):
        market_builder.build(
            spec=direct_breadth_spec(), as_of=NOW, information_cutoff=CUTOFF,
            policy=POLICY, code_revision=CODE, feature_inputs={"breadth": early_publication},
        )

    forged_request = published_feature(
        store,
        feature_registry,
        value="0.62",
        request_hash_override=digest(canonical({"forged": True})),
    )
    with pytest.raises(DataQualityError, match="MARKET_STATE_FEATURE_MANIFEST_INVALID"):
        market_builder.build(
            spec=direct_breadth_spec(), as_of=NOW, information_cutoff=CUTOFF,
            policy=POLICY, code_revision=CODE, feature_inputs={"breadth": forged_request},
        )

    bronze_parent = published_feature(
        store,
        feature_registry,
        value="0.63",
        use_bronze_market_parent=True,
    )
    with pytest.raises(DataQualityError, match="MARKET_STATE_FEATURE_MANIFEST_INVALID"):
        market_builder.build(
            spec=direct_breadth_spec(), as_of=NOW, information_cutoff=CUTOFF,
            policy=POLICY, code_revision=CODE, feature_inputs={"breadth": bronze_parent},
        )


def test_direct_binding_rejects_unregistered_feature_definition(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    empty_registry = FeatureRegistry()
    market_builder = MarketStateBuilder(store, empty_registry)
    with pytest.raises(DataQualityError, match="MARKET_STATE_FEATURE_DEFINITION_MISSING"):
        market_builder.build(
            spec=direct_breadth_spec(), as_of=NOW, information_cutoff=CUTOFF,
            policy=POLICY, code_revision=CODE,
        )


def test_spec_rejects_implicit_composition_or_normalization():
    with pytest.raises(ValueError, match="MARKET_STATE_DIRECT_BINDING_INVALID"):
        MarketStateComponentSpec(
            component_id="trend", semantic_type="TREND_STATE", unit="1",
            formula_version="trend.zscore@1", source_feature_id="market.momentum_12_1",
            source_instrument_id="MARKET", normalization=StateNormalization.Z_SCORE,
        )
