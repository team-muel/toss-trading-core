from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus
from asset_management.states import (
    CompanyStateEngine, MarketStateEngine, OperationalState, PortfolioStateEngine,
    StateComponent, StatePolicy, StateRepository, StateType, SystemStateEngine,
)
from asset_management.states.company import COMPANY_COMPONENTS
from asset_management.states.market import MARKET_COMPONENTS
from asset_management.states.portfolio import PORTFOLIO_COMPONENTS
from asset_management.states.system import SYSTEM_COMPONENTS


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
POLICY = StatePolicy("state-policy-v1", Decimal("0.8"), Decimal("0.5"), 300)


def components(names, *, confidence="0.9", quality=QualityStatus.VALID, freshness=10,
               values=None):
    values = values or {}
    return {name: StateComponent(values.get(name, Decimal("0.1")), Decimal(confidence),
                                 quality, freshness, (f"feature.{name}",)) for name in names}


def test_four_state_engines_are_separate_and_preserve_all_components():
    engines = (MarketStateEngine(), CompanyStateEngine(), PortfolioStateEngine(), SystemStateEngine())
    contracts = (MARKET_COMPONENTS, COMPANY_COMPONENTS, PORTFOLIO_COMPONENTS, SYSTEM_COMPONENTS)
    assert [engine.state_type for engine in engines] == list(StateType)
    assert [engine.component_names for engine in engines] == list(contracts)
    ids = set()
    for engine, names in zip(engines, contracts):
        values = {name: "NORMAL" for name in names} if engine.state_type is StateType.SYSTEM else None
        snapshot = engine.build(as_of=NOW, components=components(names, values=values), policy=POLICY,
                                code_revision="git:abcdef0")
        assert tuple(snapshot.components) == names
        assert snapshot.state_type is engine.state_type
        ids.add(snapshot.state_id)
    assert len(ids) == 4


def test_market_state_stays_continuous_and_regime_is_optional():
    values = {"growth": Decimal("0.4"), "inflation": Decimal("-0.2"),
              "credit": Decimal("0.6"), "trend": Decimal("0.8"),
              "volatility": Decimal("-0.1")}
    state = MarketStateEngine().build(as_of=NOW, components=components(MARKET_COMPONENTS, values=values),
                                      policy=POLICY, code_revision="git:abcdef0")
    assert state.regime_label is None
    assert state.components["growth"].value == Decimal("0.4")
    labelled = MarketStateEngine().build(
        as_of=NOW, components=components(MARKET_COMPONENTS, values=values), policy=POLICY,
        code_revision="git:abcdef0", derive_regime=True)
    assert labelled.regime_label == "EXPANSION"
    assert len(labelled.components) == 9


def test_each_component_can_be_recomputed_without_changing_others():
    engine = MarketStateEngine()
    original = engine.build(as_of=NOW, components=components(MARKET_COMPONENTS),
                            policy=POLICY, code_revision="git:abcdef0")
    replacement = StateComponent(Decimal("0.8"), Decimal("0.95"), QualityStatus.VALID,
                                 5, ("feature.growth.new",))
    revised = engine.recompute_component(original, component_name="growth", component=replacement,
                                         as_of=NOW, policy=POLICY, code_revision="git:abcdef1")
    assert revised.components["growth"] == replacement
    for name in set(MARKET_COMPONENTS) - {"growth"}:
        assert revised.components[name] == original.components[name]
    assert revised.state_id != original.state_id


def test_portfolio_state_preserves_truth_and_risk_structures():
    values = {
        "nav": Decimal("100000"), "cash_by_currency": {"USD": Decimal("1000")},
        "current_weights": {"SPY": Decimal("0.6")}, "sector_exposure": {"TECH": Decimal("0.3")},
        "factor_exposure": {"VALUE": Decimal("0.2")}, "currency_exposure": {"USD": Decimal("1")},
        "open_orders": ["order-1"], "risk_contribution": {"SPY": Decimal("0.5")},
        "reserved_cash": Decimal("100"), "unsettled_cash": Decimal("50"),
    }
    state = PortfolioStateEngine().build(as_of=NOW, components=components(PORTFOLIO_COMPONENTS, values=values),
                                         policy=POLICY, code_revision="git:abcdef0")
    assert state.components["cash_by_currency"].value["USD"] == Decimal("1000")
    assert state.components["open_orders"].value == ["order-1"]


def test_system_state_only_produces_operational_restrictions():
    engine = SystemStateEngine()
    normal = engine.build(as_of=NOW, components=components(
        SYSTEM_COMPONENTS, values={name: "NORMAL" for name in SYSTEM_COMPONENTS}),
        policy=POLICY, code_revision="git:abcdef0")
    assert normal.operational_state is OperationalState.NORMAL
    halted_values = {name: "NORMAL" for name in SYSTEM_COMPONENTS} | {"broker_health": "BLOCKED"}
    halted = engine.build(as_of=NOW, components=components(SYSTEM_COMPONENTS, values=halted_values),
                          policy=POLICY, code_revision="git:abcdef0")
    assert halted.operational_state is OperationalState.HALTED
    assert halted.risk_multiplier == "0"
    encoded = json.dumps(halted.payload())
    assert "BUY" not in encoded and "SELL" not in encoded
    unknown_values = {name: "NORMAL" for name in SYSTEM_COMPONENTS} | {"data_health": "UNKNOWN"}
    unknown = engine.build(as_of=NOW, components=components(SYSTEM_COMPONENTS, values=unknown_values),
                           policy=POLICY, code_revision="git:abcdef0")
    assert unknown.operational_state is OperationalState.NO_NEW_TRADES
    with pytest.raises(DataQualityError, match="SYSTEM_HEALTH_VALUE_INVALID"):
        engine.build(as_of=NOW, components=components(
            SYSTEM_COMPONENTS, values={name: "BUY" for name in SYSTEM_COMPONENTS}),
            policy=POLICY, code_revision="git:abcdef0")


def test_uncertainty_changes_risk_instead_of_only_describing_it():
    reduced = MarketStateEngine().build(
        as_of=NOW, components=components(MARKET_COMPONENTS, confidence="0.4"),
        policy=POLICY, code_revision="git:abcdef0")
    assert reduced.operational_state is OperationalState.REDUCED_RISK
    assert reduced.risk_multiplier == "0.50"
    caution = MarketStateEngine().build(
        as_of=NOW, components=components(MARKET_COMPONENTS, confidence="0.7"),
        policy=POLICY, code_revision="git:abcdef0")
    assert caution.operational_state is OperationalState.CAUTION
    assert caution.risk_multiplier == "0.75"


def test_stale_or_invalid_component_blocks_new_trades():
    stale = MarketStateEngine().build(
        as_of=NOW, components=components(MARKET_COMPONENTS, freshness=301),
        policy=POLICY, code_revision="git:abcdef0")
    assert stale.operational_state is OperationalState.NO_NEW_TRADES
    assert stale.risk_multiplier == "0"
    invalid = components(MARKET_COMPONENTS)
    invalid["credit"] = StateComponent(None, Decimal("0"), QualityStatus.CONFLICT, 10,
                                       ("feature.credit",))
    blocked = MarketStateEngine().build(as_of=NOW, components=invalid, policy=POLICY,
                                        code_revision="git:abcdef0")
    assert blocked.quality_status is QualityStatus.CONFLICT
    assert blocked.operational_state is OperationalState.NO_NEW_TRADES


def test_common_fields_and_feature_lineage_are_complete():
    state = CompanyStateEngine().build(as_of=NOW, components=components(COMPANY_COMPONENTS),
                                       policy=POLICY, code_revision="git:abcdef0")
    assert state.as_of == NOW.isoformat()
    assert state.confidence == "0.9" and state.quality_status is QualityStatus.VALID
    assert state.freshness == 10 and len(state.input_feature_ids) == len(COMPANY_COMPONENTS)
    assert state.policy_version == "state-policy-v1" and state.code_revision == "git:abcdef0"


def test_state_snapshot_is_deterministic_and_immutable(tmp_path):
    engine = MarketStateEngine()
    values = components(MARKET_COMPONENTS)
    first = engine.build(as_of=NOW, components=values, policy=POLICY, code_revision="git:abcdef0")
    second = engine.build(as_of=NOW, components=values, policy=POLICY, code_revision="git:abcdef0")
    assert first == second
    repository = StateRepository(ImmutableDatasetStore(tmp_path, credentials_classified=True))
    assert repository.publish(first) == repository.publish(second) == first.state_id
    path = tmp_path / "catalog" / "state-snapshots" / f"{first.state_id}.json"
    assert json.loads(path.read_text(encoding="utf-8"))["components"]["growth"]["value"] == "0.1"


def test_incomplete_components_and_regime_misuse_fail_closed():
    with pytest.raises(DataQualityError, match="STATE_COMPONENTS_INCOMPLETE"):
        MarketStateEngine().build(as_of=NOW, components={}, policy=POLICY,
                                  code_revision="git:abcdef0")
    with pytest.raises(DataQualityError, match="REGIME_ONLY_AVAILABLE_FOR_MARKET_STATE"):
        CompanyStateEngine().build(as_of=NOW, components=components(COMPANY_COMPONENTS),
                                   policy=POLICY, code_revision="git:abcdef0", derive_regime=True)
    invalid = components(MARKET_COMPONENTS, values={"growth": "RISK_ON"})
    with pytest.raises(DataQualityError, match="CONTINUOUS_STATE_VALUE_INVALID"):
        MarketStateEngine().build(as_of=NOW, components=invalid, policy=POLICY,
                                  code_revision="git:abcdef0")
