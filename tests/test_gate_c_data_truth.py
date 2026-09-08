from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    REQUIRED_DATA_CHECKS, AcceptanceDecision, CheckEvidence,
    DataTruthGateInput, evaluate_data_truth_gate,
)


EVIDENCE = {
    "SILVER_RAW_MANIFEST_LINEAGE_VERIFIED": (
        "github-actions:33995650766",
        "pytest:tests/test_phase8_immutable_datasets.py::test_gold_lineage_and_missing_conflicting_parents",
        "pytest:tests/test_phase8_immutable_datasets.py::test_raw_first_lineage_utc_secrets_and_dedup",
    ),
    "SOURCE_TIME_VINTAGE_QUALITY_FIELDS_COMPLETE": (
        "pytest:tests/test_phase8_immutable_datasets.py::test_manifest_schema_matches_serialized_contract",
        "pytest:tests/test_phase9_data_collection.py::test_company_filings_enforce_priority_and_all_timestamps",
        "pytest:tests/test_phase9_data_collection.py::test_daily_etf_prices_require_complete_fields_and_canonical_mapping",
        "pytest:tests/test_phase9_data_collection.py::test_macro_minimum_set_and_release_times",
    ),
    "INVALID_QUALITY_NEVER_NORMALIZED": (
        "pytest:tests/test_phase10_data_quality.py::test_cross_source_conflict_is_recorded_and_never_averaged",
        "pytest:tests/test_phase10_data_quality.py::test_schema_timezone_and_missing_are_not_filled_with_zero",
    ),
    "SOURCE_HEALTH_FALLBACK_QUARANTINE_VERIFIED": (
        "pytest:tests/test_phase10_data_quality.py::test_degraded_source_reduces_confidence_but_unknown_blocks",
        "pytest:tests/test_phase10_data_quality.py::test_missing_conflict_and_quarantine_propagate_to_no_trade",
        "pytest:tests/test_phase10_data_quality.py::test_stale_source_blocks_features_and_decision",
        "pytest:tests/test_phase8_immutable_datasets.py::test_failures_preserve_raw_and_health",
    ),
    "MARKET_FX_RISK_FREE_MACRO_COMPANY_PIT_PASSED": (
        "pytest:tests/test_phase9_data_collection.py::test_company_filings_enforce_priority_and_all_timestamps",
        "pytest:tests/test_phase9_data_collection.py::test_daily_etf_prices_require_complete_fields_and_canonical_mapping",
        "pytest:tests/test_phase9_data_collection.py::test_fx_separates_reporting_and_execution_quotes",
        "pytest:tests/test_phase9_data_collection.py::test_macro_minimum_set_and_release_times",
        "pytest:tests/test_phase9_data_collection.py::test_risk_free_curve_requires_all_four_horizons",
    ),
    "MISSING_HISTORICAL_CONSENSUS_REMAINS_UNKNOWN": (
        "pytest:tests/test_phase9_data_collection.py::test_analyst_estimates_require_historical_known_snapshot",
        "pytest:tests/test_phase9_data_collection.py::test_consensus_surprise_requires_real_pre_release_snapshot",
    ),
    "PROVIDER_LICENSE_METADATA_PRESENT": (
        "pytest:tests/test_phase8_immutable_datasets.py::test_raw_first_lineage_utc_secrets_and_dedup",
        "pytest:tests/test_phase9_data_collection.py::test_cross_provider_price_context_requires_non_weakened_combined_license",
    ),
}


def passing_input():
    return DataTruthGateInput(
        datetime(2026, 9, 6, tzinfo=timezone.utc), "e610400",
        {name: CheckEvidence(True, EVIDENCE[name]) for name in REQUIRED_DATA_CHECKS},
    )


def test_all_data_truth_checks_pass_with_bound_evidence():
    result = evaluate_data_truth_gate(passing_input())
    assert result.decision is AcceptanceDecision.PASS
    assert result.reason_codes == ()
    assert len(result.evidence_artifact_ids) == 18


@pytest.mark.parametrize("failed_check", REQUIRED_DATA_CHECKS)
def test_every_data_check_fails_closed(failed_check):
    inputs = passing_input()
    checks = dict(inputs.checks)
    checks[failed_check] = CheckEvidence(False, EVIDENCE[failed_check])
    result = evaluate_data_truth_gate(replace(inputs, checks=checks))
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == (f"CHECK_FAILED:{failed_check}",)


def test_missing_unknown_or_empty_evidence_is_rejected():
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        DataTruthGateInput(datetime.now(timezone.utc), "revision", {})
    with pytest.raises(InvariantViolation, match="TIME_NOT_AWARE"):
        DataTruthGateInput(datetime.now(), "revision", passing_input().checks)
    with pytest.raises(InvariantViolation, match="CHECK_UNKNOWN"):
        CheckEvidence(None, ("run",))
    with pytest.raises(InvariantViolation, match="EVIDENCE_INVALID"):
        CheckEvidence(True, ())


def test_result_is_deterministic_when_check_mapping_order_changes():
    inputs = passing_input()
    reversed_checks = dict(reversed(tuple(inputs.checks.items())))
    assert evaluate_data_truth_gate(inputs) == evaluate_data_truth_gate(
        replace(inputs, checks=reversed_checks)
    )


def test_recorded_pass_is_exact_and_schema_complete():
    root = Path(__file__).parents[1]
    result = evaluate_data_truth_gate(passing_input())
    actual = asdict(result)
    actual["decision"] = actual["decision"].value
    actual["reason_codes"] = list(actual["reason_codes"])
    actual["evidence_artifact_ids"] = list(actual["evidence_artifact_ids"])
    recorded = json.loads((root / "docs/evidence/gate_c_data_truth_2026-09-06.json").read_text())
    schema = json.loads((root / "schemas/data_truth_acceptance.schema.json").read_text())
    assert actual == recorded
    assert set(schema["required"]) == set(asdict(result))
