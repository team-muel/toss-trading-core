from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import DataQualityError
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.features.models import FeatureSnapshot
from asset_management.quality.models import QualityStatus
from asset_management.states import (
    CompanyStateEngine, MarketStateEngine, OperationalState, PortfolioStateEngine,
    StateComponent, StateFeatureInput, StateNormalization, StatePolicy, StateRepository,
    StateType, SystemStateEngine,
)
from asset_management.states.company import COMPANY_COMPONENTS
from asset_management.states.market import MARKET_COMPONENTS
from asset_management.states.portfolio import PORTFOLIO_COMPONENTS
from asset_management.states.system import SYSTEM_COMPONENTS


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(minutes=1)
VALIDITY = SignalValidity(21, 21, NOW + timedelta(days=30), DecayProfile.STEP)
POLICY = StatePolicy("state-policy-v1", Decimal("0.8"), Decimal("0.5"), 300)


def identifier(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()


def feature_input(name: str, value: object, quality: QualityStatus, *, suffix: str = "") -> StateFeatureInput:
    tag = f"{name}:{suffix}" if suffix else name
    snapshot = FeatureSnapshot(
        feature_run_id=identifier("feature-run", tag),
        instrument_id="STATE_INPUT",
        feature_id=f"feature.{name}",
        as_of=NOW.isoformat(),
        information_cutoff=CUTOFF.isoformat(),
        value=str(value) if value is not None else None,
        quality_status=quality.value,
        input_manifest_ids=(identifier("source-manifest", tag),),
        parameter_set_id="feature-params-v1",
        parent_state_id=None,
        code_revision="git:abcdef0",
        validity=VALIDITY,
    )
    return StateFeatureInput(snapshot, identifier("feature-manifest", tag))


def components(names, *, state_type=StateType.MARKET, confidence="0.9",
               quality=QualityStatus.VALID, freshness=10, values=None,
               normalization=None, feature_lineage=None):
    values = values or {}
    result = {}
    for name in names:
        value = values.get(name, Decimal("0.1"))
        if state_type is StateType.SYSTEM:
            norm = StateNormalization.CATEGORICAL
            semantic_type = "SYSTEM_HEALTH"
            unit = "status"
        elif state_type is StateType.PORTFOLIO:
            norm = (StateNormalization.STRUCTURED
                    if isinstance(value, (dict, list, tuple)) else StateNormalization.RAW)
            semantic_type = "PORTFOLIO_STATE"
            unit = "native"
        else:
            norm = normalization or StateNormalization.RAW
            semantic_type = f"{name.upper()}_STATE"
            unit = "1"
        has_feature_lineage = (state_type in {StateType.MARKET, StateType.COMPANY}
                               if feature_lineage is None else feature_lineage)
        feature = feature_input(name, value, quality) if has_feature_lineage else None
        evidence_id = feature.manifest_id if feature is not None else identifier("evidence", name)
        result[name] = StateComponent(
            value=value,
            component_id=name,
            semantic_type=semantic_type,
            unit=unit,
            normalization=norm,
            as_of=NOW.isoformat(),
            information_cutoff=CUTOFF.isoformat(),
            confidence=Decimal(confidence),
            quality_status=quality,
            freshness_seconds=freshness,
            input_evidence_ids=(evidence_id,),
            parameter_set_id="state-components-v1",
            formula_version=f"{name}@1",
            input_features=((feature,) if feature is not None else ()),
        )
    return result


def build(engine, values, *, code_revision="git:abcdef0"):
    return engine.build(
        as_of=NOW, information_cutoff=CUTOFF, components=values, policy=POLICY,
        code_revision=code_revision,
    )


def test_four_state_engines_are_separate_and_preserve_all_components():
    engines = (MarketStateEngine(), CompanyStateEngine(), PortfolioStateEngine(), SystemStateEngine())
    contracts = (MARKET_COMPONENTS, COMPANY_COMPONENTS, PORTFOLIO_COMPONENTS, SYSTEM_COMPONENTS)
    assert [engine.state_type for engine in engines] == list(StateType)
    assert [engine.component_names for engine in engines] == list(contracts)
    ids = set()
    for engine, names in zip(engines, contracts):
        values = {name: "NORMAL" for name in names} if engine.state_type is StateType.SYSTEM else None
        snapshot = build(engine, components(names, state_type=engine.state_type, values=values))
        assert tuple(snapshot.components) == names
        assert snapshot.state_type is engine.state_type
        ids.add(snapshot.state_id)
    assert len(ids) == 4


def test_market_state_stays_continuous_without_generic_regime_inference():
    values = {"growth": Decimal("0.4"), "inflation": Decimal("-0.2"),
              "credit": Decimal("0.6"), "trend": Decimal("0.8"),
              "volatility": Decimal("-0.1")}
    raw_components = components(MARKET_COMPONENTS, values=values)
    engine = MarketStateEngine()
    state = build(engine, raw_components)

    assert state.regime_label is None
    assert state.components["growth"].value == Decimal("0.4")
    assert len(state.components) == 9
    assert not hasattr(engine, "_derive_regime")


def test_feature_snapshot_and_manifest_stay_atomically_bound_to_component():
    state = build(MarketStateEngine(), components(MARKET_COMPONENTS))
    growth = state.components["growth"]
    payload = growth.payload()
    assert len(payload["input_features"]) == 1
    reference = payload["input_features"][0]
    assert reference["snapshot"]["feature_id"] == "feature.growth"
    assert reference["snapshot"]["feature_run_id"] == growth.input_features[0].snapshot.feature_run_id
    assert reference["manifest_id"] == growth.input_features[0].manifest_id
    assert reference["snapshot"]["input_manifest_ids"] == list(
        growth.input_features[0].snapshot.input_manifest_ids)


def test_feature_evidence_and_quality_cannot_be_forged_upward():
    source = feature_input("growth", Decimal("0.1"), QualityStatus.CONFLICT, suffix="bad-source")
    base = components(MARKET_COMPONENTS)["growth"]
    with pytest.raises(ValueError, match="STATE_FEATURE_QUALITY_UPGRADE_FORBIDDEN"):
        replace(
            base, quality_status=QualityStatus.VALID, input_features=(source,),
            input_evidence_ids=(source.manifest_id,))
    valid = feature_input("growth", Decimal("0.1"), QualityStatus.VALID, suffix="valid-source")
    with pytest.raises(ValueError, match="STATE_FEATURE_EVIDENCE_MISSING"):
        replace(base, input_features=(valid,), input_evidence_ids=(identifier("other", "evidence"),))


def test_each_component_can_be_recomputed_without_changing_others():
    engine = MarketStateEngine()
    original = build(engine, components(MARKET_COMPONENTS))
    replacement_feature = feature_input("growth", Decimal("0.8"), QualityStatus.VALID, suffix="new")
    replacement = replace(
        original.components["growth"], value=Decimal("0.8"), confidence=Decimal("0.95"),
        freshness_seconds=5, input_features=(replacement_feature,),
        input_evidence_ids=(replacement_feature.manifest_id,),
    )
    revised = engine.recompute_component(
        original, component_name="growth", component=replacement, as_of=NOW,
        information_cutoff=CUTOFF, policy=POLICY, code_revision="git:abcdef1")
    assert revised.components["growth"] == replacement
    for name in set(MARKET_COMPONENTS) - {"growth"}:
        assert revised.components[name] == original.components[name]
    assert revised.state_id != original.state_id


def test_portfolio_state_preserves_truth_and_risk_structures_without_fake_feature_lineage():
    values = {
        "nav": Decimal("100000"), "cash_by_currency": {"USD": Decimal("1000")},
        "current_weights": {"SPY": Decimal("0.6")}, "sector_exposure": {"TECH": Decimal("0.3")},
        "factor_exposure": {"VALUE": Decimal("0.2")}, "currency_exposure": {"USD": Decimal("1")},
        "open_orders": ["order-1"], "variance_contribution": {"SPY": Decimal("0.04")},
        "volatility_contribution": {"SPY": Decimal("0.2")},
        "reserved_cash": Decimal("100"), "unsettled_cash": Decimal("50"),
    }
    state = build(PortfolioStateEngine(), components(
        PORTFOLIO_COMPONENTS, state_type=StateType.PORTFOLIO, values=values))
    assert state.components["cash_by_currency"].value["USD"] == Decimal("1000")
    assert state.components["open_orders"].value == ["order-1"]
    assert state.input_feature_ids == ()
    assert state.input_feature_manifest_ids == ()
    assert state.input_evidence_ids


def test_system_state_only_produces_operational_restrictions():
    engine = SystemStateEngine()
    normal = build(engine, components(
        SYSTEM_COMPONENTS, state_type=StateType.SYSTEM,
        values={name: "NORMAL" for name in SYSTEM_COMPONENTS}))
    assert normal.operational_state is OperationalState.NORMAL
    halted_values = {name: "NORMAL" for name in SYSTEM_COMPONENTS} | {"broker_health": "BLOCKED"}
    halted = build(engine, components(
        SYSTEM_COMPONENTS, state_type=StateType.SYSTEM, values=halted_values))
    assert halted.operational_state is OperationalState.HALTED
    assert halted.risk_multiplier == "0"
    encoded = json.dumps(halted.payload())
    assert "BUY" not in encoded and "SELL" not in encoded
    unknown_values = {name: "NORMAL" for name in SYSTEM_COMPONENTS} | {"data_health": "UNKNOWN"}
    unknown = build(engine, components(
        SYSTEM_COMPONENTS, state_type=StateType.SYSTEM, values=unknown_values))
    assert unknown.operational_state is OperationalState.NO_NEW_TRADES
    with pytest.raises(DataQualityError, match="SYSTEM_HEALTH_VALUE_INVALID"):
        build(engine, components(
            SYSTEM_COMPONENTS, state_type=StateType.SYSTEM,
            values={name: "BUY" for name in SYSTEM_COMPONENTS}))


def test_uncertainty_changes_risk_instead_of_only_describing_it():
    reduced = build(MarketStateEngine(), components(MARKET_COMPONENTS, confidence="0.4"))
    assert reduced.operational_state is OperationalState.REDUCED_RISK
    assert reduced.risk_multiplier == "0.50"
    caution = build(MarketStateEngine(), components(MARKET_COMPONENTS, confidence="0.7"))
    assert caution.operational_state is OperationalState.CAUTION
    assert caution.risk_multiplier == "0.75"


def test_stale_or_invalid_component_blocks_new_trades():
    stale = build(MarketStateEngine(), components(MARKET_COMPONENTS, freshness=301))
    assert stale.operational_state is OperationalState.NO_NEW_TRADES
    assert stale.risk_multiplier == "0"
    invalid = components(MARKET_COMPONENTS)
    invalid_feature = feature_input("credit", None, QualityStatus.CONFLICT, suffix="conflict")
    invalid["credit"] = replace(
        invalid["credit"], value=None, confidence=Decimal("0"),
        quality_status=QualityStatus.CONFLICT, input_features=(invalid_feature,),
        input_evidence_ids=(invalid_feature.manifest_id,))
    blocked = build(MarketStateEngine(), invalid)
    assert blocked.quality_status is QualityStatus.CONFLICT
    assert blocked.operational_state is OperationalState.NO_NEW_TRADES


def test_common_fields_preserve_pit_and_feature_lineage():
    state = build(CompanyStateEngine(), components(
        COMPANY_COMPONENTS, state_type=StateType.COMPANY))
    assert state.schema_version == "state-snapshot-v2"
    assert state.as_of == NOW.isoformat()
    assert state.information_cutoff == CUTOFF.isoformat()
    assert state.confidence == "0.9" and state.quality_status is QualityStatus.VALID
    assert state.freshness == 10 and len(state.input_feature_ids) == len(COMPANY_COMPONENTS)
    assert len(state.input_feature_run_ids) == len(COMPANY_COMPONENTS)
    assert len(state.input_feature_manifest_ids) == len(COMPANY_COMPONENTS)
    assert len(state.input_data_manifest_ids) == len(COMPANY_COMPONENTS)
    assert len(state.input_evidence_ids) == len(COMPANY_COMPONENTS)
    assert state.policy_version == "state-policy-v1" and state.code_revision == "git:abcdef0"


def test_state_snapshot_is_deterministic_and_semantic_metadata_changes_identity(tmp_path):
    engine = MarketStateEngine()
    values = components(MARKET_COMPONENTS)
    first = build(engine, values)
    second = build(engine, values)
    assert first == second
    changed = dict(values)
    changed["growth"] = replace(changed["growth"], normalization=StateNormalization.Z_SCORE)
    assert build(engine, changed).state_id != first.state_id

    repository = StateRepository(ImmutableDatasetStore(tmp_path, credentials_classified=True))
    assert repository.publish(first) == repository.publish(second) == first.state_id
    path = tmp_path / "catalog" / "state-snapshots" / f"{first.state_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "state-snapshot-v2"
    assert payload["information_cutoff"] == CUTOFF.isoformat()
    assert payload["components"]["growth"]["normalization"] == "RAW"
    assert payload["input_data_manifest_ids"] == list(first.input_data_manifest_ids)


def test_component_time_and_lineage_fail_closed():
    with pytest.raises(ValueError, match="STATE_COMPONENT_CUTOFF_AFTER_AS_OF"):
        StateComponent(
            value=Decimal("0.1"), component_id="growth", semantic_type="GROWTH_STATE",
            unit="1", normalization=StateNormalization.RAW, as_of=NOW.isoformat(),
            information_cutoff=(NOW + timedelta(seconds=1)).isoformat(),
            confidence=Decimal("0.9"), quality_status=QualityStatus.VALID,
            freshness_seconds=1, input_evidence_ids=(identifier("evidence", "future"),),
            parameter_set_id="p@1", formula_version="growth@1", input_features=(),
        )

    values = components(MARKET_COMPONENTS)
    values["growth"] = replace(values["growth"], input_features=())
    with pytest.raises(DataQualityError, match="STATE_COMPONENT_UNAVAILABLE_INVALID"):
        build(MarketStateEngine(), values)

    values = components(MARKET_COMPONENTS)
    values["growth"] = replace(
        values["growth"], information_cutoff=(CUTOFF + timedelta(seconds=10)).isoformat())
    with pytest.raises(DataQualityError, match="STATE_COMPONENT_CONTEXT_MISMATCH"):
        build(MarketStateEngine(), values)


def test_expired_or_future_feature_reference_fails_closed():
    snapshot = FeatureSnapshot(
        feature_run_id=identifier("feature-run", "expired"), instrument_id="STATE_INPUT",
        feature_id="feature.growth", as_of=NOW.isoformat(), information_cutoff=CUTOFF.isoformat(),
        value="0.1", quality_status=QualityStatus.VALID.value,
        input_manifest_ids=(identifier("source-manifest", "expired"),),
        parameter_set_id="feature-params-v1", parent_state_id=None,
        code_revision="git:abcdef0",
        validity=SignalValidity(21, 21, NOW, DecayProfile.STEP),
    )
    with pytest.raises(ValueError, match="STATE_FEATURE_TIME_INVALID"):
        StateFeatureInput(snapshot, identifier("feature-manifest", "expired"))

    future_snapshot = replace(
        feature_input("growth", Decimal("0.1"), QualityStatus.VALID).snapshot,
        as_of=(NOW + timedelta(seconds=1)).isoformat())
    future_ref = StateFeatureInput(future_snapshot, identifier("feature-manifest", "future"))
    with pytest.raises(ValueError, match="STATE_FEATURE_CONTEXT_INVALID"):
        replace(components(MARKET_COMPONENTS)["growth"], input_features=(future_ref,),
                input_evidence_ids=(future_ref.manifest_id,))


def test_component_id_and_normalization_contracts_fail_closed():
    values = components(MARKET_COMPONENTS)
    values["growth"] = replace(values["growth"], component_id="inflation")
    with pytest.raises(DataQualityError, match="STATE_COMPONENT_ID_MISMATCH"):
        build(MarketStateEngine(), values)

    system = components(
        SYSTEM_COMPONENTS, state_type=StateType.SYSTEM,
        values={name: "NORMAL" for name in SYSTEM_COMPONENTS})
    system["broker_health"] = replace(
        system["broker_health"], normalization=StateNormalization.RAW)
    with pytest.raises(DataQualityError, match="SYSTEM_STATE_NORMALIZATION_INVALID"):
        build(SystemStateEngine(), system)


def test_incomplete_components_and_categorical_market_values_fail_closed():
    with pytest.raises(DataQualityError, match="STATE_COMPONENTS_INCOMPLETE"):
        MarketStateEngine().build(
            as_of=NOW, information_cutoff=CUTOFF, components={}, policy=POLICY,
            code_revision="git:abcdef0")
    invalid = components(MARKET_COMPONENTS, values={"growth": "RISK_ON"})
    with pytest.raises(DataQualityError, match="CONTINUOUS_STATE_VALUE_INVALID"):
        build(MarketStateEngine(), invalid)
