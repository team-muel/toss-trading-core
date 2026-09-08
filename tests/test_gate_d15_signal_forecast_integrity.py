from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    AcceptanceDecision, CheckEvidence, REQUIRED_SIGNAL_FORECAST_CHECKS,
    SignalForecastIntegrityGateInput, evaluate_signal_forecast_integrity_gate,
)


EVIDENCE = {
    "FEATURE_SIGNAL_SEMANTIC_SEPARATION_VERIFIED": (
        "pytest:tests/test_phase_m35_signal_registry.py::test_signal_contract_is_complete_versioned_and_separate_from_features",
        "pytest:tests/test_phase_m35_signal_registry.py::test_signal_uses_only_pit_feature_snapshots_and_publishes_separate_value",
    ),
    "SIGNAL_CONTRACT_LINEAGE_HORIZON_VALIDITY_VERIFIED": (
        "pytest:tests/test_phase_m35_signal_registry.py::test_signal_snapshot_hash_and_feature_manifest_lineage_cannot_be_fabricated",
        "pytest:tests/test_phase_m35_signal_registry.py::test_current_universe_future_feature_transform_and_history_gaps_fail_closed",
    ),
    "CROSS_SECTIONAL_SIGNAL_DIAGNOSTICS_VERIFIED": (
        "pytest:tests/test_phase_m35_signal_diagnostics.py::test_cross_sectional_diagnostics_store_pit_metrics_and_post_cost_value",
        "pytest:tests/test_phase_m35_signal_diagnostics.py::test_ic_decay_stores_separate_horizon_metrics",
    ),
    "ETF_TIME_SERIES_OOS_CALIBRATION_UTILITY_VERIFIED": (
        "pytest:tests/test_phase_m35_signal_diagnostics.py::test_time_series_prioritizes_calibration_utility_and_stability",
        "pytest:tests/test_phase_m35_signal_diagnostics.py::test_time_series_future_outcome_and_forged_signal_fail_closed",
    ),
    "PIT_NORMALIZATION_NEUTRALIZATION_VERIFIED": (
        "pytest:tests/test_phase_m35_signal_neutralization.py::test_neutralization_preserves_pit_transform_lineage_and_incremental_metrics",
        "pytest:tests/test_phase_m35_signal_neutralization.py::test_future_outcome_and_snapshot_mismatch_fail_closed",
    ),
    "SIGNAL_REDUNDANCY_FACTOR_INCREMENTAL_POWER_VERIFIED": (
        "pytest:tests/test_phase_m35_signal_neutralization.py::test_neutralization_preserves_pit_transform_lineage_and_incremental_metrics",
        "pytest:tests/test_phase_m35_signal_diagnostics.py::test_cross_sectional_diagnostics_store_pit_metrics_and_post_cost_value",
    ),
    "SIGNAL_FORECAST_OOS_UNCERTAINTY_LINEAGE_VERIFIED": (
        "pytest:tests/test_phase_m35_signal_forecast_calibration.py::test_time_ordered_calibration_emits_cost_aware_forecast_components",
        "pytest:tests/test_phase_m35_signal_forecast_calibration.py::test_minimum_history_and_future_outcome_fail_closed",
    ),
    "FORECAST_COMBINATION_DIVERSIFICATION_COST_STABILITY_VERIFIED": (
        "pytest:tests/test_phase_m35_forecast_combination.py::test_combiner_uses_neutralization_oos_cost_and_correlation",
        "pytest:tests/test_phase_m35_forecast_combination.py::test_future_oos_evidence_and_parameter_conflicts_fail_closed",
    ),
    "STRATEGY_VERSION_REFERENCES_VERIFIED": (
        "pytest:tests/test_phase_m35_strategy_registry.py::test_strategy_contract_lifecycle_attribution_and_live_fail_closed",
        "pytest:tests/test_phase_m35_strategy_registry.py::test_definition_changes_cannot_silently_swap_and_invalid_lifecycle_fails_closed",
    ),
    "WEAK_SIGNAL_SHRINK_OR_ABSTAIN_VERIFIED": (
        "pytest:tests/test_phase_m35_signal_forecast_calibration.py::test_time_ordered_calibration_emits_cost_aware_forecast_components",
        "pytest:tests/test_phase_m35_signal_forecast_calibration.py::test_minimum_history_and_future_outcome_fail_closed",
        "pytest:tests/test_phase_m35_signal_registry.py::test_missing_coverage_returns_abstain_without_publishing_signal",
    ),
    "LOOK_AHEAD_SURVIVORSHIP_LEAKAGE_BLOCKED": (
        "pytest:tests/test_phase_m35_signal_registry.py::test_current_universe_future_feature_transform_and_history_gaps_fail_closed",
        "pytest:tests/test_phase_m35_signal_diagnostics.py::test_cross_sectional_leakage_and_coverage_fail_closed",
        "pytest:tests/test_phase_m35_signal_neutralization.py::test_future_outcome_and_snapshot_mismatch_fail_closed",
    ),
}


def passing_input():
    return SignalForecastIntegrityGateInput(
        datetime(2026, 9, 6, tzinfo=timezone.utc), "beea069",
        {name: CheckEvidence(True, EVIDENCE[name]) for name in REQUIRED_SIGNAL_FORECAST_CHECKS},
    )


def test_all_signal_forecast_integrity_checks_bind_evidence_and_allow_m4():
    result = evaluate_signal_forecast_integrity_gate(passing_input())
    assert result.decision is AcceptanceDecision.PASS
    assert result.reason_codes == () and result.permits_m4_execution
    assert len(result.evidence_artifact_ids) == len({item for values in EVIDENCE.values() for item in values})


@pytest.mark.parametrize("failed_check", REQUIRED_SIGNAL_FORECAST_CHECKS)
def test_each_signal_forecast_integrity_failure_blocks_m4(failed_check):
    inputs = passing_input()
    checks = dict(inputs.checks)
    checks[failed_check] = CheckEvidence(False, EVIDENCE[failed_check])
    result = evaluate_signal_forecast_integrity_gate(replace(inputs, checks=checks))
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == (f"CHECK_FAILED:{failed_check}",)
    assert not result.permits_m4_execution


def test_unknown_time_missing_check_or_empty_evidence_fails_closed():
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        SignalForecastIntegrityGateInput(datetime.now(timezone.utc), "revision", {})
    with pytest.raises(InvariantViolation, match="TIME_NOT_AWARE"):
        SignalForecastIntegrityGateInput(datetime.now(), "revision", passing_input().checks)
    with pytest.raises(InvariantViolation, match="CHECK_UNKNOWN"):
        CheckEvidence(None, ("run",))
    with pytest.raises(InvariantViolation, match="EVIDENCE_INVALID"):
        CheckEvidence(True, ())


def test_result_is_deterministic_when_check_mapping_order_changes():
    inputs = passing_input()
    reversed_checks = dict(reversed(tuple(inputs.checks.items())))
    assert evaluate_signal_forecast_integrity_gate(inputs) == evaluate_signal_forecast_integrity_gate(
        replace(inputs, checks=reversed_checks)
    )


def test_recorded_pass_is_exact_and_schema_complete():
    root = Path(__file__).parents[1]
    result = evaluate_signal_forecast_integrity_gate(passing_input())
    actual = asdict(result)
    actual["decision"] = actual["decision"].value
    actual["reason_codes"] = list(actual["reason_codes"])
    actual["evidence_artifact_ids"] = list(actual["evidence_artifact_ids"])
    recorded = json.loads((root / "docs/evidence/gate_d15_signal_forecast_integrity_2026-09-06.json").read_text())
    schema = json.loads((root / "schemas/signal_forecast_integrity_acceptance.schema.json").read_text())
    assert actual == recorded
    assert set(schema["required"]) == set(asdict(result))
