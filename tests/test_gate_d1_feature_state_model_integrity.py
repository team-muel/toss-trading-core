from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    REQUIRED_FEATURE_STATE_MODEL_CHECKS, AcceptanceDecision, CheckEvidence,
    FeatureStateModelIntegrityGateInput, evaluate_feature_state_model_integrity_gate,
)


EVIDENCE = {
    "FEATURE_PIT_LEAKAGE_BLOCKED": (
        "pytest:tests/test_phase11_feature_store.py::test_historical_standardization_excludes_current_and_future_information",
        "pytest:tests/test_phase11_feature_store.py::test_winsorization_uses_exact_historical_cross_section",
        "pytest:tests/test_phase11_feature_store.py::test_future_quarter_and_missing_history_fail_closed_without_gold",
    ),
    "HORIZON_VALIDITY_DECAY_CONTRACT_VERIFIED": (
        "pytest:tests/test_phase11_horizon_contract.py::test_unknown_or_incomplete_contract_fails_closed",
        "pytest:tests/test_phase11_horizon_contract.py::test_step_linear_and_exponential_decay_are_deterministic",
        "pytest:tests/test_phase11_horizon_contract.py::test_horizon_alignment_rejects_forecast_and_holding_mismatch",
    ),
    "FOUR_STATE_SNAPSHOTS_REPRODUCIBLE": (
        "pytest:tests/test_phase12_state_engines.py::test_four_state_engines_are_separate_and_preserve_all_components",
        "pytest:tests/test_phase12_state_engines.py::test_state_snapshot_is_deterministic_and_immutable",
    ),
    "CALCULATION_LINEAGE_TO_RAW_MANIFEST_VERIFIED": (
        "pytest:tests/test_phase11_calculation_lineage.py::test_final_estimate_traces_to_verified_immutable_raw_manifest",
        "pytest:tests/test_phase11_calculation_lineage.py::test_raw_manifest_must_exist_be_bronze_and_match_content",
    ),
    "QUALITY_FRESHNESS_CONFIDENCE_PROPAGATION_VERIFIED": (
        "pytest:tests/test_phase10_data_quality.py::test_stale_source_blocks_features_and_decision",
        "pytest:tests/test_phase10_data_quality.py::test_missing_conflict_and_quarantine_propagate_to_no_trade",
        "pytest:tests/test_phase10_data_quality.py::test_degraded_source_reduces_confidence_but_unknown_blocks",
        "pytest:tests/test_phase12_state_engines.py::test_state_quality_and_freshness_reduce_or_block_risk",
    ),
    "MODEL_APPROVED_SCOPE_ENFORCED": (
        "pytest:tests/test_phase11_model_registry.py::test_scope_status_review_and_registry_changes_cannot_be_bypassed",
        "pytest:tests/test_phase11_model_registry.py::test_future_transition_never_activates_a_model_before_its_effective_time",
        "pytest:tests/test_phase13_asset_pricing.py::test_capm_cannot_run_without_matching_registry_authorization",
    ),
    "IDENTICAL_INPUT_VERSION_STATE_MODEL_OUTPUT_HASH_REPRODUCIBLE": (
        "pytest:tests/test_phase11_feature_store.py::test_same_inputs_and_parameters_produce_identical_feature_and_lineage",
        "pytest:tests/test_phase12_state_engines.py::test_state_snapshot_is_deterministic_and_immutable",
        "pytest:tests/test_phase13_asset_pricing.py::test_pricing_output_hash_is_deterministic_and_bound_to_model_version",
    ),
}


def passing_input():
    return FeatureStateModelIntegrityGateInput(
        datetime(2026, 9, 6, tzinfo=timezone.utc), "a80176c",
        {name: CheckEvidence(True, EVIDENCE[name]) for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS},
    )


def test_all_feature_state_model_integrity_checks_pass_with_bound_evidence():
    result = evaluate_feature_state_model_integrity_gate(passing_input())
    assert result.decision is AcceptanceDecision.PASS
    assert result.reason_codes == ()
    assert len(result.evidence_artifact_ids) == 19


@pytest.mark.parametrize("failed_check", REQUIRED_FEATURE_STATE_MODEL_CHECKS)
def test_every_feature_state_model_check_fails_closed(failed_check):
    inputs = passing_input()
    checks = dict(inputs.checks)
    checks[failed_check] = CheckEvidence(False, EVIDENCE[failed_check])
    result = evaluate_feature_state_model_integrity_gate(replace(inputs, checks=checks))
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == (f"CHECK_FAILED:{failed_check}",)


def test_missing_unknown_or_empty_evidence_is_rejected():
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        FeatureStateModelIntegrityGateInput(datetime.now(timezone.utc), "revision", {})
    with pytest.raises(InvariantViolation, match="TIME_NOT_AWARE"):
        FeatureStateModelIntegrityGateInput(datetime.now(), "revision", passing_input().checks)
    with pytest.raises(InvariantViolation, match="CHECK_UNKNOWN"):
        CheckEvidence(None, ("run",))
    with pytest.raises(InvariantViolation, match="EVIDENCE_INVALID"):
        CheckEvidence(True, ())


def test_result_is_deterministic_when_check_mapping_order_changes():
    inputs = passing_input()
    reversed_checks = dict(reversed(tuple(inputs.checks.items())))
    assert evaluate_feature_state_model_integrity_gate(inputs) == evaluate_feature_state_model_integrity_gate(
        replace(inputs, checks=reversed_checks)
    )


def test_recorded_pass_is_exact_and_schema_complete():
    root = Path(__file__).parents[1]
    result = evaluate_feature_state_model_integrity_gate(passing_input())
    actual = asdict(result)
    actual["decision"] = actual["decision"].value
    actual["reason_codes"] = list(actual["reason_codes"])
    actual["evidence_artifact_ids"] = list(actual["evidence_artifact_ids"])
    recorded = json.loads((root / "docs/evidence/gate_d1_feature_state_model_integrity_2026-09-06.json").read_text())
    schema = json.loads((root / "schemas/feature_state_model_integrity_acceptance.schema.json").read_text())
    assert actual == recorded
    assert set(schema["required"]) == set(asdict(result))
