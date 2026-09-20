from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from asset_management.domain.errors import DataQualityError
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.features.models import FeatureSnapshot
from asset_management.quality.models import QualityStatus
from asset_management.states import (
    MarketStateEngine,
    OperationalState,
    StateComponent,
    StateFeatureInput,
    StateNormalization,
    StatePolicy,
)
from asset_management.states.market import MARKET_COMPONENTS


NOW = datetime(2026, 9, 14, 9, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(minutes=1)
VALIDITY = SignalValidity(21, 21, NOW + timedelta(days=30), DecayProfile.STEP)
POLICY = StatePolicy("state-policy-v1", Decimal("0.8"), Decimal("0.5"), 300)


def ident(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()


def feature_component(name: str, value: Decimal = Decimal("0.1")) -> StateComponent:
    snapshot = FeatureSnapshot(
        feature_run_id=ident("run", name),
        instrument_id="STATE_INPUT",
        feature_id=f"market.{name}",
        as_of=NOW.isoformat(),
        information_cutoff=CUTOFF.isoformat(),
        value=str(value),
        quality_status=QualityStatus.VALID.value,
        input_manifest_ids=(ident("source", name),),
        parameter_set_id="feature-v1",
        parent_state_id=None,
        code_revision="git:abcdef0",
        validity=VALIDITY,
    )
    ref = StateFeatureInput(snapshot, ident("feature-manifest", name))
    return StateComponent(
        value=value,
        component_id=name,
        semantic_type=f"{name.upper()}_STATE",
        unit="1",
        normalization=StateNormalization.RAW,
        as_of=NOW.isoformat(),
        information_cutoff=CUTOFF.isoformat(),
        confidence=Decimal("0.9"),
        quality_status=QualityStatus.VALID,
        freshness_seconds=60,
        input_evidence_ids=(ref.manifest_id,),
        parameter_set_id="market-state-v1",
        formula_version=f"{name}@1",
        input_features=(ref,),
    )


def unavailable_component(name: str, *, reason: str = "UNMAPPED_COMPONENT") -> StateComponent:
    return StateComponent(
        value=None,
        component_id=name,
        semantic_type=f"{name.upper()}_STATE",
        unit="1",
        normalization=StateNormalization.RAW,
        as_of=NOW.isoformat(),
        information_cutoff=CUTOFF.isoformat(),
        confidence=Decimal("0"),
        quality_status=QualityStatus.MISSING,
        freshness_seconds=0,
        input_evidence_ids=(ident("unavailable", name, reason),),
        parameter_set_id="market-state-v1",
        formula_version=f"{name}@1",
        input_features=(),
        reason_code=reason,
    )


def market_components() -> dict[str, StateComponent]:
    return {name: feature_component(name) for name in MARKET_COMPONENTS}


def build(values: dict[str, StateComponent]):
    return MarketStateEngine().build(
        as_of=NOW,
        information_cutoff=CUTOFF,
        components=values,
        policy=POLICY,
        code_revision="git:abcdef0",
    )


def test_unmapped_component_is_explicit_without_fabricated_feature_lineage():
    values = market_components()
    values["growth"] = unavailable_component("growth")

    state = build(values)

    assert state.components["growth"].value is None
    assert state.components["growth"].reason_code == "UNMAPPED_COMPONENT"
    assert state.components["growth"].confidence == Decimal("0")
    assert state.components["growth"].input_features == ()
    assert "market.growth" not in state.input_feature_ids
    assert state.quality_status is QualityStatus.MISSING
    assert state.operational_state is OperationalState.NO_NEW_TRADES
    assert state.risk_multiplier == "0"


def test_successful_market_component_cannot_omit_feature_lineage():
    values = market_components()
    values["growth"] = replace(values["growth"], input_features=())

    with pytest.raises(DataQualityError, match="STATE_COMPONENT_UNAVAILABLE_INVALID"):
        build(values)


def test_unavailable_component_requires_none_value_blocking_quality_zero_confidence_and_reason():
    values = market_components()
    missing = unavailable_component("growth")

    for invalid in (
        replace(missing, reason_code=None),
        replace(missing, value=Decimal("0.1")),
        replace(missing, quality_status=QualityStatus.VALID),
        replace(missing, confidence=Decimal("0.1")),
    ):
        values["growth"] = invalid
        with pytest.raises(DataQualityError, match="STATE_COMPONENT_UNAVAILABLE_INVALID"):
            build(values)


def test_unavailable_reason_changes_identity_without_generic_regime_inference():
    first_values = market_components()
    first_values["growth"] = unavailable_component("growth", reason="UNMAPPED_COMPONENT")
    second_values = dict(first_values)
    second_values["growth"] = unavailable_component("growth", reason="SOURCE_NOT_APPROVED")

    first = build(first_values)
    second = build(second_values)

    assert first.state_id != second.state_id
    assert first.regime_label is None
    assert first.operational_state is OperationalState.NO_NEW_TRADES
    assert not hasattr(MarketStateEngine(), "_derive_regime")
