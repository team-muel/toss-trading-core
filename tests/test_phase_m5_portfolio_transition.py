from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from asset_management.domain.errors import DataQualityError
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.portfolio import (PortfolioTarget, TransitionMode, TransitionPlanner,
                                        TransitionPlanningInput, TransitionPrerequisite)


D = Decimal
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def portfolio(weights, stage):
    return PortfolioTarget(("SELL", "BUY", "CASH"), tuple(map(D, weights)), stage)


def planning_input(**changes):
    values = {
        "current": portfolio((".5", ".1", ".4"), "CURRENT"),
        "executable_target": portfolio((".2", ".4", ".4"), "EXECUTABLE_TARGET"),
        "cash_instrument": "CASH",
        "forecast_values": {"SELL": D("-.04"), "BUY": D(".04"), "CASH": D(0)},
        "produced_at": NOW,
        "evaluated_at": NOW,
        "signal_validity": SignalValidity(63, 21, NOW + timedelta(days=10), DecayProfile.LINEAR),
        "next_rebalance_at": NOW + timedelta(days=7),
        "liquidity_horizon_at": NOW + timedelta(days=6),
        "stage_interval": timedelta(days=1),
        "stage_count": 2,
        "settlement_available_at": NOW + timedelta(hours=12),
        "upcoming_event_times": (),
        "open_order_exposure": {},
        "available_cash_weight": D(".1"),
        "linear_costs": {"SELL": D(".001"), "BUY": D(".001"), "CASH": D(0)},
        "impact_costs": {"SELL": D(".1"), "BUY": D(".1"), "CASH": D(0)},
        "tax_costs": {"SELL": D(".001"), "BUY": D(".001"), "CASH": D(0)},
        "liquidity_capacities": {"SELL": D(".5"), "BUY": D(".5"), "CASH": D(1)},
        "cost_curve_key": "cost-curve@1",
        "tax_policy_key": "tax-policy@1",
        "liquidity_policy_key": "liquidity-policy@1",
    }
    values.update(changes)
    return TransitionPlanningInput(**values)


def test_staged_plan_is_a_weight_plan_with_sell_settlement_buy_dependencies_and_reassessment():
    plan = TransitionPlanner().plan(planning_input())
    assert plan.mode is TransitionMode.STAGED
    assert plan.staged_expected_utility > plan.immediate_expected_utility > 0
    assert [step.action.value for step in plan.steps] == ["SELL", "BUY", "SELL", "BUY"]
    first_sell, first_buy, second_sell, second_buy = plan.steps
    assert first_buy.depends_on_step_ids == (first_sell.step_id,)
    assert {TransitionPrerequisite.SELL_FILL_CONFIRMED,
            TransitionPrerequisite.SETTLED_CASH_CONFIRMED} <= set(first_buy.prerequisites)
    assert set(second_sell.depends_on_step_ids) == {first_sell.step_id, first_buy.step_id}
    assert second_buy.requires_reassessment and first_buy.requires_reassessment
    assert not ({"order_id", "quantity", "limit_price", "side"} & set(plan.steps[0].__dataclass_fields__))
    assert plan.payload()["planning_input"]["cost_curve_key"] == "cost-curve@1"
    assert plan.payload()["plan_hash"] == plan.plan_hash


def test_immediate_plan_wins_when_forecast_decay_outweighs_staging_cost_reduction():
    plan = TransitionPlanner().plan(planning_input(
        impact_costs={"SELL": D(0), "BUY": D(0), "CASH": D(0)},
    ))
    assert plan.mode is TransitionMode.IMMEDIATE
    assert plan.immediate_expected_utility > plan.staged_expected_utility > 0
    assert len(plan.steps) == 2


@pytest.mark.parametrize("changes, reason", [
    ({"open_order_exposure": {"SELL": D(".05")}}, "OPEN_ORDER_EXPOSURE"),
    ({"upcoming_event_times": (NOW + timedelta(days=1),)}, "UPCOMING_EVENT_DEFER"),
    ({"signal_validity": SignalValidity(63, 21, NOW, DecayProfile.STEP)}, "FORECAST_EXPIRED"),
    ({"available_cash_weight": D(".01")}, "CASH_CONSTRAINT"),
])
def test_unknown_or_unexecutable_transition_inputs_defer_without_steps(changes, reason):
    plan = TransitionPlanner().plan(planning_input(**changes))
    assert plan.mode is TransitionMode.DEFER
    assert plan.steps == ()
    assert reason in plan.reason_codes


def test_staging_respects_per_step_liquidity_capacity_when_immediate_transition_does_not():
    plan = TransitionPlanner().plan(planning_input(
        impact_costs={"SELL": D(0), "BUY": D(0), "CASH": D(0)},
        liquidity_capacities={"SELL": D(".2"), "BUY": D(".2"), "CASH": D(1)},
    ))
    assert plan.mode is TransitionMode.STAGED
    assert all(abs(step.weight_delta) <= D(".2") for step in plan.steps)


def test_invalid_schedule_or_incomplete_cost_curve_fails_closed():
    with pytest.raises(DataQualityError, match="TRANSITION_SCHEDULE_INVALID"):
        planning_input(stage_count=1)
    with pytest.raises(DataQualityError, match="TRANSITION_COST_CURVE_INVALID"):
        planning_input(linear_costs={"SELL": D(0), "BUY": D(0)})


def test_schema_covers_complete_serialized_transition_plan():
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/portfolio_transition.schema.json").read_text())
    plan = TransitionPlanner().plan(planning_input())
    assert set(schema["required"]) == set(plan.payload())
    assert schema["$defs"]["step"]["required"] == list(plan.steps[0].payload())
