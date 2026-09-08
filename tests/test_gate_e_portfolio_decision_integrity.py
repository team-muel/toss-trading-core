from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    AcceptanceDecision, CheckEvidence, PortfolioDecisionIntegrityGateInput,
    REQUIRED_PORTFOLIO_DECISION_INTEGRITY_CHECKS, evaluate_portfolio_decision_integrity_gate,
)


EVIDENCE = {
    "NAV_LIABILITY_CAPITAL_BASIS_NO_DOUBLE_COUNTING_VERIFIED": (
        "pytest:tests/test_phase16_portfolio_construction.py::test_capital_basis_return_semantics_and_stability_evidence_fail_closed",),
    "ABSOLUTE_STRATEGIC_FORECAST_TOTAL_RETURN_OBJECTIVE_VERIFIED": (
        "pytest:tests/test_phase16_portfolio_construction.py::test_optimizer_moves_toward_forecast_total_return_but_returns_weights_only",),
    "ACTIVE_BENCHMARK_FORECAST_TOTAL_RETURN_OBJECTIVE_VERIFIED": (
        "pytest:tests/test_phase16_portfolio_construction.py::test_active_and_model_alpha_modes_require_a_matching_benchmark_mandate",),
    "MODEL_RELATIVE_ALPHA_MODE_AUTHORIZED_AND_FACTOR_NEUTRAL_VERIFIED": (
        "pytest:tests/test_phase16_portfolio_construction.py::test_active_and_model_alpha_modes_require_a_matching_benchmark_mandate",),
    "PRICING_BASELINE_AND_COVARIANCE_PENALTY_SEPARATED": (
        "pytest:tests/test_phase16_portfolio_construction.py::test_objective_has_half_variance_and_no_double_counting",),
    "CONSTRAINTS_AND_INFEASIBLE_FAIL_CLOSED": (
        "pytest:tests/test_phase16_portfolio_construction.py::test_all_constraints_and_infeasible_are_explicit",),
    "COST_TAX_LIQUIDITY_NO_TRADE_UNIT_ALIGNMENT_VERIFIED": (
        "pytest:tests/test_phase16_portfolio_construction.py::test_no_trade_band_and_economic_gate_preserve_three_targets",
        "pytest:tests/test_phase_m5_portfolio_transition.py::test_invalid_schedule_or_incomplete_cost_curve_fails_closed"),
    "GROSS_NET_TRANSACTION_TAX_DRAG_NOT_DOUBLE_COUNTED": (
        "pytest:tests/test_phase16_portfolio_construction.py::test_objective_has_half_variance_and_no_double_counting",
        "pytest:tests/test_phase_m5_economic_decision_journal.py::test_record_preserves_four_states_lineage_three_targets_and_noncollapsed_return_semantics"),
    "ORDER_AND_LIQUIDATION_CAPACITY_SEPARATED": (
        "pytest:tests/test_phase_m5_portfolio_transition.py::test_staging_respects_per_step_liquidity_capacity_when_immediate_transition_does_not",
        "pytest:tests/test_phase4_cash_position_settlement.py::test_sell_reservation_above_settled_quantity_blocks"),
    "RISK_GOVERNOR_HARD_GATE_AND_FORECAST_AUTHORITY_ENFORCED": (
        "pytest:tests/test_phase17_risk_governor.py::test_every_hard_condition_blocks",
        "pytest:tests/test_phase17_risk_governor.py::test_same_semantic_inputs_produce_same_decision_and_lineage_hash"),
    "TRANSITION_PREREQUISITES_VALIDITY_AND_COST_CURVE_VERIFIED": (
        "pytest:tests/test_phase_m5_portfolio_transition.py::test_staged_plan_is_a_weight_plan_with_sell_settlement_buy_dependencies_and_reassessment",
        "pytest:tests/test_phase_m5_portfolio_transition.py::test_unknown_or_unexecutable_transition_inputs_defer_without_steps"),
    "TRANSITION_IMMEDIATE_STAGED_UTILITY_AND_REASSESSMENT_VERIFIED": (
        "pytest:tests/test_phase_m5_portfolio_transition.py::test_staged_plan_is_a_weight_plan_with_sell_settlement_buy_dependencies_and_reassessment",
        "pytest:tests/test_phase_m5_portfolio_transition.py::test_immediate_plan_wins_when_forecast_decay_outweighs_staging_cost_reduction"),
    "MANUAL_OVERRIDE_AUDIT_REPLAY_VERIFIED": (
        "pytest:tests/test_phase_m5_manual_overrides.py::test_journal_is_append_only_and_exact_replay_preserves_the_state_event",),
    "DECISION_RETURN_SEMANTICS_SEPARATED": (
        "pytest:tests/test_phase_m5_economic_decision_journal.py::test_record_preserves_four_states_lineage_three_targets_and_noncollapsed_return_semantics",),
    "MANDATE_BENCHMARK_RISK_AUTHORITY_VERSION_PINNED": (
        "pytest:tests/test_phase_m4_investor_mandate.py::test_mandate_freezes_benchmark_risk_budget_and_optimizer_ranges",
        "pytest:tests/test_phase_m5_economic_decision_journal.py::test_registry_mandate_and_benchmark_versions_must_match_ama_124_authority"),
    "OPTIMUM_STABILITY_PERTURBATION_VERIFIED": (
        "pytest:tests/test_phase16_portfolio_construction.py::test_capital_basis_return_semantics_and_stability_evidence_fail_closed",),
    "DETERMINISTIC_DECISION_VERIFIED": (
        "pytest:tests/test_phase17_risk_governor.py::test_same_semantic_inputs_produce_same_decision_and_lineage_hash",
        "pytest:tests/test_phase_m5_economic_decision_journal.py::test_journal_replays_exact_initial_record_and_keeps_outcome_pending_before_maturity"),
}


def passing_input():
    return PortfolioDecisionIntegrityGateInput(
        datetime(2026, 9, 7, tzinfo=timezone.utc), "gate-e@1",
        {name: CheckEvidence(True, EVIDENCE[name]) for name in REQUIRED_PORTFOLIO_DECISION_INTEGRITY_CHECKS},
    )


def test_complete_evidence_allows_m6_readiness_only():
    result = evaluate_portfolio_decision_integrity_gate(passing_input())
    assert result.decision is AcceptanceDecision.PASS
    assert result.reason_codes == () and result.permits_m6_execution
    assert len(result.evidence_artifact_ids) == len({item for values in EVIDENCE.values() for item in values})


@pytest.mark.parametrize("failed_check", REQUIRED_PORTFOLIO_DECISION_INTEGRITY_CHECKS)
def test_each_failed_or_unknown_gate_e_check_blocks_m6(failed_check):
    inputs = passing_input()
    checks = dict(inputs.checks)
    checks[failed_check] = CheckEvidence(False, EVIDENCE[failed_check])
    result = evaluate_portfolio_decision_integrity_gate(replace(inputs, checks=checks))
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == (f"CHECK_FAILED:{failed_check}",)
    assert not result.permits_m6_execution


def test_gate_e_requires_exact_check_set_and_reproducible_payload():
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        PortfolioDecisionIntegrityGateInput(datetime.now(timezone.utc), "gate-e@1", {})
    with pytest.raises(InvariantViolation, match="TIME_NOT_AWARE"):
        PortfolioDecisionIntegrityGateInput(datetime.now(), "gate-e@1", passing_input().checks)
    inputs = passing_input()
    assert evaluate_portfolio_decision_integrity_gate(inputs) == evaluate_portfolio_decision_integrity_gate(
        replace(inputs, checks=dict(reversed(tuple(inputs.checks.items())))))


def test_recorded_pass_and_schema_remain_complete():
    root = Path(__file__).parents[1]
    result = evaluate_portfolio_decision_integrity_gate(passing_input())
    actual = asdict(result)
    actual["decision"] = actual["decision"].value
    actual["reason_codes"] = list(actual["reason_codes"])
    actual["evidence_artifact_ids"] = list(actual["evidence_artifact_ids"])
    assert actual == json.loads((root / "docs/evidence/gate_e_portfolio_decision_integrity_2026-09-07.json").read_text())
    schema = json.loads((root / "schemas/portfolio_decision_integrity_acceptance.schema.json").read_text())
    assert set(schema["required"]) == set(asdict(result))
