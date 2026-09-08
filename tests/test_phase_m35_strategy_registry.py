from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import (
    CapitalRiskBudget, StrategyAttribution, StrategyDefinition, StrategyRegistry,
    StrategyRuntimeMode, StrategyStatus,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def definition(strategy_id="quality-value"):
    return StrategyDefinition(
        strategy_id=strategy_id, version="1", name="Quality Value Overlay",
        economic_thesis="Quality and value forecasts earn a diversified risk premium.",
        investable_universe_key="us-large-cap@2026-09-06",
        signal_keys=("quality-profitability@1", "value-relative-strength@1"),
        forecast_combination_key="forecast-overlay@1", pricing_model_key="capm@1",
        risk_model_key="factor-risk@1", portfolio_policy_key="long-only@1",
        execution_policy_key="paper-passive@1", benchmark="SPY",
        budget=CapitalRiskBudget(Decimal("0.30"), Decimal("0.20"), Decimal("1"), Decimal("0.25")),
        supported_horizon=21, currency="USD",
        allowed_runtime_modes=(StrategyRuntimeMode.RESEARCH, StrategyRuntimeMode.PAPER,
                               StrategyRuntimeMode.SHADOW, StrategyRuntimeMode.LIVE),
        effective_from=NOW, effective_to=NOW + timedelta(days=365),
    )


def live_registry():
    registry = StrategyRegistry()
    strategy = definition()
    registry.register(strategy)
    for index, status in enumerate((StrategyStatus.CANDIDATE, StrategyStatus.PAPER,
                                    StrategyStatus.SHADOW, StrategyStatus.LIVE), start=1):
        registry.transition(strategy.key, status, effective_at=NOW + timedelta(seconds=index),
                            reason=f"promote to {status.value}", evidence_ids=(f"evidence:{index}",))
    return registry, strategy


def attribution(strategy_key):
    body = {
        "strategy_key": strategy_key, "as_of": NOW.isoformat(),
        "gross_return": "0.020", "implementation_cost": "0.001",
        "net_return": "0.019", "benchmark_return": "0.010",
        "forecast_component_ids": ["a" * 64, "b" * 64],
    }
    return StrategyAttribution(
        strategy_key=strategy_key, as_of=NOW, gross_return=Decimal("0.020"),
        implementation_cost=Decimal("0.001"), net_return=Decimal("0.019"),
        benchmark_return=Decimal("0.010"), forecast_component_ids=("a" * 64, "b" * 64),
        attribution_hash=digest(canonical(body)),
    )


def test_strategy_contract_lifecycle_attribution_and_live_fail_closed(tmp_path):
    registry, strategy = live_registry()
    payload = registry.payload()
    assert payload["strategies"][0]["status"] == "LIVE"
    assert [item["to_status"] for item in payload["transitions"]] == [
        "CANDIDATE", "PAPER", "SHADOW", "LIVE"]
    assert registry.status_at(strategy.key, at=NOW) is StrategyStatus.RESEARCH
    with pytest.raises(InvariantViolation, match="STRATEGY_LIVE_RUNTIME_DISABLED"):
        registry.authorize(strategy.key, StrategyRuntimeMode.LIVE, at=NOW + timedelta(seconds=5))
    registry.transition(strategy.key, StrategyStatus.DEGRADED, effective_at=NOW + timedelta(seconds=6),
                        reason="attribution degradation", evidence_ids=("attribution:1",))
    registry.transition(strategy.key, StrategyStatus.PAPER, effective_at=NOW + timedelta(seconds=7),
                        reason="recovery requires paper validation", evidence_ids=("review:1",))
    authorization = registry.authorize(strategy.key, StrategyRuntimeMode.PAPER,
                                       at=NOW + timedelta(seconds=8))
    registry.require_authorization(authorization, strategy_key=strategy.key,
                                   runtime_mode=StrategyRuntimeMode.PAPER,
                                   at=NOW + timedelta(seconds=8))
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    attribution_id = registry.record_attribution(store, attribution(strategy.key))
    assert json.loads((tmp_path / "catalog" / "strategy-attribution" /
                       f"{attribution_id}.json").read_text())["net_return"] == "0.019"
    first = registry.publish(store)
    assert first == registry.publish(store)
    assert json.loads((tmp_path / "catalog" / "strategy-registry" /
                       f"{first}.json").read_text()) == registry.payload()


@pytest.mark.parametrize("change, reason", [
    ({"signal_keys": ()}, "STRATEGY_SIGNAL_KEYS_INVALID"),
    ({"effective_to": NOW}, "STRATEGY_EFFECTIVE_WINDOW_INVALID"),
    ({"allowed_runtime_modes": ()}, "STRATEGY_DEFINITION_INVALID"),
])
def test_incomplete_strategy_metadata_fails_closed(change, reason):
    with pytest.raises(InvariantViolation, match=reason):
        replace(definition(), **change)


def test_invalid_strategy_budget_fails_closed():
    with pytest.raises(InvariantViolation, match="STRATEGY_BUDGET_INVALID"):
        CapitalRiskBudget(Decimal("0.30"), Decimal("0.20"), Decimal("1"), Decimal("-0.01"))


def test_definition_changes_cannot_silently_swap_and_invalid_lifecycle_fails_closed():
    registry = StrategyRegistry()
    strategy = definition()
    registry.register(strategy)
    with pytest.raises(InvariantViolation, match="STRATEGY_DEFINITION_CONFLICT"):
        registry.register(replace(strategy, execution_policy_key="aggressive@2"))
    with pytest.raises(InvariantViolation, match="STRATEGY_LIFECYCLE_TRANSITION_INVALID"):
        registry.transition(strategy.key, StrategyStatus.LIVE, effective_at=NOW,
                            reason="skip validation", evidence_ids=("evidence",))
    with pytest.raises(InvariantViolation, match="STRATEGY_TRANSITION_EVIDENCE_INVALID"):
        registry.transition(strategy.key, StrategyStatus.CANDIDATE, effective_at=NOW,
                            reason="candidate", evidence_ids=())


def test_registry_change_invalidates_authorization_and_future_transition_cannot_be_used_early():
    registry = StrategyRegistry()
    strategy = definition()
    registry.register(strategy)
    registry.transition(strategy.key, StrategyStatus.CANDIDATE,
                        effective_at=NOW + timedelta(hours=1), reason="schedule candidate",
                        evidence_ids=("evidence:1",))
    registry.transition(strategy.key, StrategyStatus.PAPER,
                        effective_at=NOW + timedelta(hours=1, seconds=1), reason="schedule paper",
                        evidence_ids=("evidence:2",))
    assert registry.status_at(strategy.key, at=NOW) is StrategyStatus.RESEARCH
    with pytest.raises(InvariantViolation, match="STRATEGY_NOT_ACTIVE_FOR_RUNTIME"):
        registry.authorize(strategy.key, StrategyRuntimeMode.PAPER, at=NOW)
    authorization = registry.authorize(strategy.key, StrategyRuntimeMode.PAPER,
                                       at=NOW + timedelta(hours=1, seconds=2))
    registry.transition(strategy.key, StrategyStatus.SHADOW,
                        effective_at=NOW + timedelta(hours=1, seconds=3), reason="new review",
                        evidence_ids=("evidence:3",))
    with pytest.raises(InvariantViolation, match="STRATEGY_AUTHORIZATION_INVALID"):
        registry.require_authorization(authorization, strategy_key=strategy.key,
                                       runtime_mode=StrategyRuntimeMode.PAPER,
                                       at=NOW + timedelta(hours=1, seconds=2))


def test_schema_matches_registry_and_strategy_contract():
    registry, _ = live_registry()
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/strategy_registry.schema.json").read_text())
    payload = registry.payload()
    assert set(schema["required"]) == set(payload)
    assert set(schema["$defs"]["strategy"]["required"]) == set(payload["strategies"][0])
    assert set(schema["$defs"]["transition"]["required"]) == set(payload["transitions"][0])
