from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    AcceptanceDecision, CheckEvidence, PricingExpectationRiskIntegrityGateInput,
    REQUIRED_PRICING_EXPECTATION_RISK_CHECKS, evaluate_pricing_expectation_risk_integrity_gate,
)


NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def inputs(*, failed=()):
    return PricingExpectationRiskIntegrityGateInput(
        NOW, "gate-d2@1", {
            name: CheckEvidence(name not in failed, (f"evidence:{name.lower()}",))
            for name in REQUIRED_PRICING_EXPECTATION_RISK_CHECKS
        },
    )


def test_d2_pass_requires_every_explicit_check_and_only_then_permits_m5():
    result = evaluate_pricing_expectation_risk_integrity_gate(inputs())
    assert result.decision is AcceptanceDecision.PASS and result.permits_m5_execution
    assert len(result.evidence_artifact_ids) == len(REQUIRED_PRICING_EXPECTATION_RISK_CHECKS)


@pytest.mark.parametrize("failed", REQUIRED_PRICING_EXPECTATION_RISK_CHECKS)
def test_each_missing_or_failed_d2_condition_blocks_m5(failed):
    result = evaluate_pricing_expectation_risk_integrity_gate(inputs(failed=(failed,)))
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == (f"CHECK_FAILED:{failed}",)
    assert not result.permits_m5_execution


def test_d2_rejects_partial_unknown_or_non_reproducible_evidence_sets():
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        PricingExpectationRiskIntegrityGateInput(NOW, "gate-d2@1", {})
    with pytest.raises(InvariantViolation, match="TIME_NOT_AWARE"):
        PricingExpectationRiskIntegrityGateInput(datetime(2026, 9, 8), "gate-d2@1", inputs().checks)
    first = evaluate_pricing_expectation_risk_integrity_gate(inputs())
    second = evaluate_pricing_expectation_risk_integrity_gate(replace(inputs(), checks=dict(reversed(tuple(inputs().checks.items())))))
    assert first == second


def test_recorded_current_d2_result_is_pass_and_schema_complete():
    root = Path(__file__).parents[1]
    recorded = json.loads((root / "docs/evidence/gate_d2_pricing_expectation_risk_2026-09-08.json").read_text())
    actual = asdict(evaluate_pricing_expectation_risk_integrity_gate(
        inputs()))
    actual["decision"] = actual["decision"].value
    actual["reason_codes"] = list(actual["reason_codes"])
    actual["evidence_artifact_ids"] = list(actual["evidence_artifact_ids"])
    assert actual == recorded
    schema = json.loads((root / "schemas/pricing_expectation_risk_integrity_acceptance.schema.json").read_text())
    assert set(schema["required"]) == set(actual)
