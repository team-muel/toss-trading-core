from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.risk import (
    EventAction, EventRiskInput, EventType, REQUIRED_STRESS_SCENARIOS,
    StressScenario, ValuationConvention, assess_event_risk, stress_loss,
    validate_scenario_set,
)
from asset_management.decisions import RiskGovernor
from test_phase17_risk_governor import inputs, policy


NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def scenario(identifier="US_EQUITY_MINUS_10"):
    return StressScenario(identifier, {"SPY": Decimal("-.10")}, {"USD/KRW": Decimal(".05")},
                          "KRW", ValuationConvention.TOTAL_RETURN, 21, "stress@1", Decimal(3), Decimal("1.5"))


def event(**changes):
    values = dict(
        event_id="fomc-2026-09", event_type=EventType.FOMC,
        scheduled_at=NOW + timedelta(days=1), as_of_utc=NOW, action=EventAction.DEFER,
        uncertainty_buffer=Decimal(".01"), liquidity_buffer=Decimal(".02"),
    )
    values.update(changes)
    return EventRiskInput(**values)


def test_stress_result_keeps_currency_valuation_horizon_and_risk_only_fields():
    result = stress_loss((Decimal(1),), ("SPY",), scenario(), currency_basis="KRW",
                         valuation_convention=ValuationConvention.TOTAL_RETURN, holding_horizon_days=21)
    assert result.loss_fraction == Decimal(".10")
    assert result.currency_basis == "KRW"
    assert result.valuation_convention is ValuationConvention.TOTAL_RETURN
    assert result.holding_horizon_days == 21
    assert "return" not in result.__dataclass_fields__ and "alpha" not in result.__dataclass_fields__


@pytest.mark.parametrize("change", (
    {"currency_basis": "USD"},
    {"valuation_convention": ValuationConvention.MARK_TO_MARKET},
    {"holding_horizon_days": 20},
))
def test_stress_context_mismatch_fails_closed(change):
    with pytest.raises(DataQualityError, match="STRESS_INPUT_CONTEXT_INVALID"):
        stress_loss((Decimal(1),), ("SPY",), scenario(), currency_basis=change.get("currency_basis", "KRW"),
                    valuation_convention=change.get("valuation_convention", ValuationConvention.TOTAL_RETURN),
                    holding_horizon_days=change.get("holding_horizon_days", 21))


def test_stress_scenarios_must_be_complete_and_explicitly_contextualized():
    all_scenarios = tuple(scenario(identifier) for identifier in REQUIRED_STRESS_SCENARIOS)
    validate_scenario_set(all_scenarios)
    with pytest.raises(DataQualityError, match="STRESS_SCENARIO_SET_INCOMPLETE"):
        validate_scenario_set(all_scenarios[:-1])
    with pytest.raises(InvariantViolation, match="STRESS_SCENARIO_CONTEXT_INVALID"):
        StressScenario("US_EQUITY_MINUS_10", {"SPY": Decimal("-.1")}, {"USD/KRW": Decimal(".05")},
                       "", ValuationConvention.TOTAL_RETURN, 21, "stress@1")


def test_event_control_constrains_decision_without_creating_return_or_weight():
    control = assess_event_risk(event(), decision_horizon_end=NOW + timedelta(days=2))
    assert control is not None and control.action is EventAction.DEFER
    assert control.uncertainty_buffer == Decimal(".01")
    assert not ({"return", "alpha", "weight"} & set(control.__dataclass_fields__))
    assert assess_event_risk(event(), decision_horizon_end=NOW + timedelta(hours=12)) is None


@pytest.mark.parametrize("action,state", [
    (EventAction.REDUCE, "REDUCE"), (EventAction.DEFER, "DEFER"), (EventAction.BLOCK, "BLOCK"),
])
def test_event_control_maps_only_to_existing_risk_governor_constraints(action, state):
    control = assess_event_risk(event(action=action), decision_horizon_end=NOW + timedelta(days=2))
    assert control is not None
    decision = RiskGovernor(policy()).decide(inputs(**control.risk_input_flags()))
    assert decision.state.value == state


def test_event_overlap_and_unknown_time_fail_closed():
    with pytest.raises(DataQualityError, match="EVENT_RISK_LINEAGE_OVERLAP"):
        event(expected_return_overlay_lineage_ids=("lineage:earnings",),
              event_penalty_lineage_ids=("lineage:earnings",))
    with pytest.raises(InvariantViolation, match="EVENT_RISK_SCHEDULE_TIME_INVALID"):
        event(scheduled_at=datetime(2026, 9, 9))
    with pytest.raises(DataQualityError, match="EVENT_RISK_HORIZON_INVALID"):
        assess_event_risk(event(), decision_horizon_end=NOW)


def test_schema_contracts_cover_risk_only_outputs():
    root = Path(__file__).parents[1]
    stress_schema = json.loads((root / "schemas/stress_scenario.schema.json").read_text())
    event_schema = json.loads((root / "schemas/event_risk_control.schema.json").read_text())
    Draft202012Validator.check_schema(stress_schema)
    Draft202012Validator.check_schema(event_schema)
    Draft202012Validator(stress_schema).validate(scenario().payload())
    control = assess_event_risk(event(), decision_horizon_end=NOW + timedelta(days=2))
    assert control is not None
    Draft202012Validator(event_schema).validate({
        "event_id": control.event_id, "action": control.action.value,
        "uncertainty_buffer": str(control.uncertainty_buffer), "liquidity_buffer": str(control.liquidity_buffer),
        "scheduled_at": control.scheduled_at.isoformat(), "event_type": control.event_type.value,
    })
