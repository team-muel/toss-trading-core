from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    AcceptanceDecision, CheckEvidence, REQUIRED_SIGNAL_FORECAST_CHECKS,
    GitSourceRevisionVerifier, SignalForecastIntegrityGateInput,
    evaluate_signal_forecast_integrity_gate,
)


TEST_SELECTORS = {
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


ROOT = Path(__file__).parents[1]
REVISION = "git:" + subprocess.run(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True,
    text=True,
).stdout.strip()


def passing_input(tmp_path, *, code_revision=REVISION, evidence_code_revision=None):
    evidence_code_revision = evidence_code_revision or code_revision
    verifier = GitSourceRevisionVerifier(ROOT)
    source = verifier.verify(code_revision)
    assert source is not None
    store = ImmutableDatasetStore(tmp_path)
    evidence = {}
    for name in REQUIRED_SIGNAL_FORECAST_CHECKS:
        identifier = store.catalog("signal-forecast-gate-evidence", {
            "schema_version": "signal-forecast-gate-evidence@1",
            "check_name": name,
            "code_revision": evidence_code_revision,
            "source_tree": source.source_tree,
        })
        evidence[name] = (f"sha256:{identifier}",)
    return (
        SignalForecastIntegrityGateInput(
            datetime(2026, 9, 6, tzinfo=timezone.utc), code_revision, evidence_code_revision,
            {name: CheckEvidence(True, evidence[name]) for name in REQUIRED_SIGNAL_FORECAST_CHECKS},
        ),
        store,
        verifier,
    )


def test_all_signal_forecast_integrity_checks_bind_evidence_and_allow_m4(tmp_path):
    inputs, store, verifier = passing_input(tmp_path)
    result = evaluate_signal_forecast_integrity_gate(
        inputs, evidence_store=store, source_revision_verifier=verifier)
    assert result.decision is AcceptanceDecision.PASS
    assert result.reason_codes == () and result.permits_m4_execution
    assert len(result.evidence_artifact_ids) == len(REQUIRED_SIGNAL_FORECAST_CHECKS)


@pytest.mark.parametrize("failed_check", REQUIRED_SIGNAL_FORECAST_CHECKS)
def test_each_signal_forecast_integrity_failure_blocks_m4(tmp_path, failed_check):
    inputs, store, verifier = passing_input(tmp_path)
    checks = dict(inputs.checks)
    checks[failed_check] = CheckEvidence(False, inputs.checks[failed_check].artifact_ids)
    result = evaluate_signal_forecast_integrity_gate(
        replace(inputs, checks=checks), evidence_store=store, source_revision_verifier=verifier)
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == (f"CHECK_FAILED:{failed_check}",)
    assert not result.permits_m4_execution


def test_unknown_time_missing_check_or_empty_evidence_fails_closed(tmp_path):
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        SignalForecastIntegrityGateInput(datetime.now(timezone.utc), "revision", "revision", {})
    with pytest.raises(InvariantViolation, match="TIME_NOT_AWARE"):
        SignalForecastIntegrityGateInput(
            datetime.now(), "revision", "revision", passing_input(tmp_path)[0].checks)
    with pytest.raises(InvariantViolation, match="CHECK_UNKNOWN"):
        CheckEvidence(None, ("run",))
    with pytest.raises(InvariantViolation, match="EVIDENCE_INVALID"):
        CheckEvidence(True, ())


def test_result_is_deterministic_when_check_mapping_order_changes(tmp_path):
    inputs, store, verifier = passing_input(tmp_path)
    reversed_checks = dict(reversed(tuple(inputs.checks.items())))
    assert evaluate_signal_forecast_integrity_gate(
        inputs, evidence_store=store, source_revision_verifier=verifier) == evaluate_signal_forecast_integrity_gate(
        replace(inputs, checks=reversed_checks), evidence_store=store, source_revision_verifier=verifier
    )


def test_historical_test_selectors_and_short_revision_cannot_permit_m4(tmp_path):
    inputs = SignalForecastIntegrityGateInput(
        datetime(2026, 9, 6, tzinfo=timezone.utc), "beea069", "beea069",
        {name: CheckEvidence(True, TEST_SELECTORS[name])
         for name in REQUIRED_SIGNAL_FORECAST_CHECKS},
    )
    result = evaluate_signal_forecast_integrity_gate(
        inputs, evidence_store=ImmutableDatasetStore(tmp_path),
        source_revision_verifier=GitSourceRevisionVerifier(ROOT))
    assert result.decision is AcceptanceDecision.FAIL
    assert not result.permits_m4_execution
    assert result.reason_codes[:2] == (
        "CODE_REVISION_UNVERIFIED", "EVIDENCE_CODE_REVISION_UNVERIFIED",
    )
    assert result.reason_codes[2] == "SOURCE_REVISION_UNVERIFIED"
    assert set(result.reason_codes[3:]) == {
        f"EVIDENCE_ARTIFACT_UNVERIFIED:{name}" for name in REQUIRED_SIGNAL_FORECAST_CHECKS
    }


def test_mismatched_exact_source_revision_cannot_permit_m4(tmp_path):
    inputs, store, verifier = passing_input(
        tmp_path, code_revision=REVISION, evidence_code_revision="git:" + "b" * 40)
    result = evaluate_signal_forecast_integrity_gate(
        inputs, evidence_store=store, source_revision_verifier=verifier)
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes[0] == "EVIDENCE_CODE_REVISION_MISMATCH"
    assert set(result.reason_codes[1:]) == {
        f"EVIDENCE_ARTIFACT_UNVERIFIED:{name}" for name in REQUIRED_SIGNAL_FORECAST_CHECKS
    }
    assert not result.permits_m4_execution


def test_unavailable_or_wrong_immutable_store_cannot_permit_m4(tmp_path):
    inputs, _, verifier = passing_input(tmp_path / "published")
    absent = evaluate_signal_forecast_integrity_gate(
        inputs, source_revision_verifier=verifier)
    wrong_store = evaluate_signal_forecast_integrity_gate(
        inputs, evidence_store=ImmutableDatasetStore(tmp_path / "other"),
        source_revision_verifier=verifier)
    assert absent.decision is AcceptanceDecision.FAIL
    assert "EVIDENCE_STORE_UNVERIFIED" in absent.reason_codes
    assert wrong_store.decision is AcceptanceDecision.FAIL
    assert set(wrong_store.reason_codes) == {
        f"EVIDENCE_ARTIFACT_UNVERIFIED:{name}" for name in REQUIRED_SIGNAL_FORECAST_CHECKS
    }
    assert not absent.permits_m4_execution and not wrong_store.permits_m4_execution


def test_nonexistent_full_sha_cannot_permit_m4(tmp_path):
    nonexistent = "git:" + "f" * 40
    store = ImmutableDatasetStore(tmp_path)
    checks = {}
    for name in REQUIRED_SIGNAL_FORECAST_CHECKS:
        identifier = store.catalog("signal-forecast-gate-evidence", {
            "schema_version": "signal-forecast-gate-evidence@1",
            "check_name": name,
            "code_revision": nonexistent,
            "source_tree": "e" * 40,
        })
        checks[name] = CheckEvidence(True, (f"sha256:{identifier}",))
    result = evaluate_signal_forecast_integrity_gate(
        SignalForecastIntegrityGateInput(
            datetime(2026, 9, 6, tzinfo=timezone.utc), nonexistent, nonexistent, checks),
        evidence_store=store, source_revision_verifier=GitSourceRevisionVerifier(ROOT))
    assert result.decision is AcceptanceDecision.FAIL
    assert "SOURCE_REVISION_UNVERIFIED" in result.reason_codes
    assert not result.permits_m4_execution


def test_real_non_head_commit_cannot_permit_m4(tmp_path):
    # CI checks out the pull-request commit at depth one, so the repository
    # under test has no resolvable parent.  Make the two real commits needed
    # for this negative-path authority check instead of weakening it there.
    repository = tmp_path / "repository"
    repository.mkdir()
    for command in (
            ["git", "init", "--quiet", str(repository)],
            ["git", "-C", str(repository), "config", "user.name", "test"],
            ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
    ):
        subprocess.run(command, check=True, capture_output=True, text=True)
    tracked_file = repository / "tracked.txt"
    tracked_file.write_text("parent\\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "commit", "--quiet", "-m", "parent"],
                   check=True, capture_output=True, text=True)
    parent = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()
    parent_tree = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", f"{parent}^{{tree}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    tracked_file.write_text("child\\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "commit", "--quiet", "-m", "child"],
                   check=True, capture_output=True, text=True)
    historical = f"git:{parent}"
    store = ImmutableDatasetStore(tmp_path)
    checks = {}
    for name in REQUIRED_SIGNAL_FORECAST_CHECKS:
        identifier = store.catalog("signal-forecast-gate-evidence", {
            "schema_version": "signal-forecast-gate-evidence@1",
            "check_name": name,
            "code_revision": historical,
            "source_tree": parent_tree,
        })
        checks[name] = CheckEvidence(True, (f"sha256:{identifier}",))
    result = evaluate_signal_forecast_integrity_gate(
        SignalForecastIntegrityGateInput(
            datetime(2026, 9, 6, tzinfo=timezone.utc), historical, historical, checks),
        evidence_store=store, source_revision_verifier=GitSourceRevisionVerifier(repository))
    assert result.decision is AcceptanceDecision.FAIL
    assert "SOURCE_REVISION_UNVERIFIED" in result.reason_codes
    assert not result.permits_m4_execution


def test_historical_record_is_not_current_acceptance_authority(tmp_path):
    recorded = json.loads((ROOT / "docs/evidence/gate_d15_signal_forecast_integrity_2026-09-06.json").read_text())
    schema = json.loads((ROOT / "schemas/signal_forecast_integrity_acceptance.schema.json").read_text())
    assert recorded["decision"] == "PASS"
    assert recorded["code_revision"] == "beea069"
    assert "evidence_code_revision" not in recorded
    assert all(item.startswith("pytest:") for item in recorded["evidence_artifact_ids"])
    assert "evidence_code_revision" in schema["required"]
    inputs, store, verifier = passing_input(tmp_path)
    assert set(schema["required"]) == set(asdict(
        evaluate_signal_forecast_integrity_gate(
            inputs, evidence_store=store, source_revision_verifier=verifier)))
