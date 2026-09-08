from dataclasses import fields, replace
from decimal import Decimal

import pytest

from asset_management.decisions import (
    DecisionJournal, DecisionState, ReasonCode, RiskGovernor, RiskGovernorPolicy, RiskInputs,
)
from asset_management.decisions.governor import HARD_BLOCKS, SOFT_REDUCTIONS
from asset_management.domain.errors import InvariantViolation, NoTrade
from asset_management.execution.intents import OrderIntent, TargetWeight


def policy() -> RiskGovernorPolicy:
    return RiskGovernorPolicy(
        "risk-v17",
        {reason: Decimal("0.75") for _, reason in SOFT_REDUCTIONS}
        | {ReasonCode.SPREAD_HIGH: Decimal("0.50")},
    )


def inputs(**changes: bool) -> RiskInputs:
    base = RiskInputs(
        runtime_run_id="run-17", portfolio_target_id="target-17",
        portfolio_target_hash="target-hash", policy_version="risk-v17",
        as_of_utc="2026-09-05T00:00:00+00:00",
        evidence_ids=("account-17", "risk-model-17", "target-17"),
    )
    return replace(base, **changes)


RISK_FLAGS = tuple(field.name for field in fields(RiskInputs) if field.type == "bool")


@pytest.mark.parametrize("field", RISK_FLAGS)
@pytest.mark.parametrize("value", [None, 0, 1, "", "false", "true", [], {}])
def test_unknown_or_coerced_risk_flags_cannot_authorize(field, value):
    with pytest.raises(NoTrade, match=f"RISK_INPUT_INVALID: {field}"):
        RiskGovernor(policy()).decide(inputs(**{field: value})).authorize()


@pytest.mark.parametrize("field", RISK_FLAGS)
def test_explicit_boolean_risk_flags_preserve_decisions(field):
    governor = RiskGovernor(policy())
    assert governor.decide(inputs(**{field: False})).state is DecisionState.ALLOW
    assert governor.decide(inputs(**{field: True})).state is not DecisionState.ALLOW


@pytest.mark.parametrize("field,reason", HARD_BLOCKS)
def test_every_hard_condition_blocks(field: str, reason: ReasonCode):
    decision = RiskGovernor(policy()).decide(inputs(**{field: True}))
    assert decision.state is DecisionState.BLOCK
    assert decision.exposure_multiplier == 0
    assert reason in decision.reason_codes
    with pytest.raises(NoTrade):
        decision.authorize()


def test_hard_block_dominates_all_soft_reductions():
    decision = RiskGovernor(policy()).decide(inputs(
        kill_switch_active=True, volatility_high=True, spread_high=True,
    ))
    assert decision.state is DecisionState.BLOCK
    assert decision.reason_codes == (ReasonCode.KILL_SWITCH_ACTIVE,)


def test_soft_conditions_reduce_by_most_conservative_explicit_cap():
    decision = RiskGovernor(policy()).decide(inputs(volatility_high=True, spread_high=True))
    assert decision.state is DecisionState.REDUCE
    assert decision.exposure_multiplier == Decimal("0.50")
    assert decision.reason_codes == (ReasonCode.VOLATILITY_HIGH, ReasonCode.SPREAD_HIGH)
    assert decision.authorize().risk_decision_id == decision.risk_decision_id
    scaled = decision.apply_to_target(
        {"SPY": Decimal("0.80"), "CASH": Decimal("0.20")}, cash_instrument_id="CASH"
    )
    assert scaled == {"SPY": Decimal("0.4000"), "CASH": Decimal("0.6000")}


def test_all_five_states_are_distinct_and_non_approved_states_fail_closed():
    governor = RiskGovernor(policy())
    decisions = (
        governor.decide(inputs()),
        governor.decide(inputs(low_confidence=True)),
        governor.decide(inputs(data_conflict=True)),
        governor.decide(inputs(evidence_insufficient=True)),
        governor.decide(inputs(defer_execution=True, event_risk_high=True)),
    )
    assert tuple(item.state for item in decisions) == tuple(DecisionState)
    for decision in decisions[2:]:
        assert decision.reason_codes
        with pytest.raises(NoTrade):
            decision.authorize()


def test_same_semantic_inputs_produce_same_decision_and_lineage_hash():
    governor = RiskGovernor(policy())
    first = governor.decide(inputs())
    reordered = replace(inputs(), evidence_ids=("target-17", "account-17", "risk-model-17"))
    second = governor.decide(reordered)
    assert first == second
    assert "alpha" not in first.__dataclass_fields__


def test_policy_is_complete_versioned_and_policy_mismatch_blocks():
    with pytest.raises(InvariantViolation, match="every soft condition"):
        RiskGovernorPolicy("risk-v17", {})
    decision = RiskGovernor(policy()).decide(replace(inputs(), policy_version="risk-v16"))
    assert decision.state is DecisionState.BLOCK
    assert decision.reason_codes == (ReasonCode.POLICY_MISMATCH,)


def test_order_intent_requires_exact_approved_decision_binding():
    approved = RiskGovernor(policy()).decide(inputs()).authorize()
    weight = (TargetWeight("SPY", Decimal("1"), Decimal("0")),)
    intent = OrderIntent("run-17", "risk-v17", "target-17", "target-hash", approved, weight, ("rebalance",))
    assert intent.risk_authorization.risk_decision_id == approved.risk_decision_id
    with pytest.raises(InvariantViolation, match="changed after risk approval"):
        OrderIntent("run-17", "risk-v17", "target-17", "changed", approved, weight, ())


def test_decision_journal_is_append_only_and_exact_replay_is_idempotent(tmp_path):
    journal = DecisionJournal(tmp_path / "risk-decisions.jsonl")
    decision = RiskGovernor(policy()).decide(inputs(volatility_high=True))
    journal.append(decision)
    journal.append(decision)
    assert journal.load() == (decision,)
    conflicting = replace(decision, state=DecisionState.ALLOW)
    with pytest.raises(InvariantViolation, match="cannot be overwritten"):
        journal.append(conflicting)
