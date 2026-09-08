from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    REQUIRED_TEMPORAL_CHECKS, AcceptanceDecision, CheckEvidence,
    TemporalTruthGateInput, evaluate_temporal_truth_gate,
)


EVIDENCE = {
    "FUTURE_SENTINEL_ZERO_FAILURES": ("pytest:tests/test_phase6_point_in_time.py::test_future_sentinel_and_same_asof_are_deterministic", "github-actions:33995472105"),
    "AVAILABLE_AFTER_AS_OF_BLOCKED": ("pytest:tests/test_phase6_point_in_time.py::test_delayed_receipt_and_ingestion_define_actual_availability",),
    "VINTAGE_REVISION_REPLAY_MATCHED": ("pytest:tests/test_phase6_point_in_time.py::test_revision_vintage_replays_original_before_revision_release",),
    "SAME_DAY_CLOSE_LEAKAGE_BLOCKED": ("pytest:tests/test_phase6_point_in_time.py::test_same_day_close_is_not_visible_to_morning_decision",),
    "PRE_RELEASE_AND_PRE_RECEIPT_ACCESS_BLOCKED": ("pytest:tests/test_phase9_data_collection.py::test_consensus_surprise_requires_real_pre_release_snapshot",),
    "DST_HOLIDAY_EARLY_CLOSE_PASSED": ("pytest:tests/test_phase7_reference.py::test_calendar_dst_early_close_and_unknown_session",),
    "POINT_IN_TIME_UNIVERSE_RESTORED": ("pytest:tests/test_phase7_reference.py::test_historical_universe_and_delisting",),
    "CORPORATE_ACTION_PRICE_SEMANTICS_VERIFIED": ("pytest:tests/test_phase7_reference.py::test_price_storage_return_and_delisting_action",),
}


def passing_input():
    return TemporalTruthGateInput(
        datetime(2026, 9, 6, tzinfo=timezone.utc), "8bf2013",
        {name: CheckEvidence(True, EVIDENCE[name]) for name in REQUIRED_TEMPORAL_CHECKS},
    )


def test_all_temporal_truth_checks_pass_with_bound_evidence():
    result = evaluate_temporal_truth_gate(passing_input())
    assert result.decision is AcceptanceDecision.PASS
    assert result.reason_codes == ()
    assert len(result.evidence_artifact_ids) == 9


@pytest.mark.parametrize("failed_check", REQUIRED_TEMPORAL_CHECKS)
def test_every_temporal_check_fails_closed(failed_check):
    inputs = passing_input()
    checks = dict(inputs.checks)
    checks[failed_check] = CheckEvidence(False, EVIDENCE[failed_check])
    result = evaluate_temporal_truth_gate(replace(inputs, checks=checks))
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == (f"CHECK_FAILED:{failed_check}",)


def test_missing_unknown_or_empty_evidence_is_rejected():
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        TemporalTruthGateInput(datetime.now(timezone.utc), "revision", {})
    with pytest.raises(InvariantViolation, match="TIME_NOT_AWARE"):
        TemporalTruthGateInput(datetime.now(), "revision", passing_input().checks)
    with pytest.raises(InvariantViolation, match="CHECK_UNKNOWN"):
        CheckEvidence(None, ("run",))
    with pytest.raises(InvariantViolation, match="EVIDENCE_INVALID"):
        CheckEvidence(True, ())


def test_recorded_pass_is_exact_and_schema_complete():
    root = __import__("pathlib").Path(__file__).parents[1]
    result = evaluate_temporal_truth_gate(passing_input())
    actual = asdict(result)
    actual["decision"] = actual["decision"].value
    actual["reason_codes"] = list(actual["reason_codes"])
    actual["evidence_artifact_ids"] = list(actual["evidence_artifact_ids"])
    recorded = json.loads((root / "docs/evidence/gate_b_temporal_truth_2026-09-06.json").read_text())
    schema = json.loads((root / "schemas/temporal_truth_acceptance.schema.json").read_text())
    assert actual == recorded
    assert set(schema["required"]) == set(asdict(result))
