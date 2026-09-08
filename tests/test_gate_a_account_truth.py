from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    REQUIRED_CHECKS, AcceptanceDecision, AccountTruthGateInput, CheckEvidence,
    evaluate_account_truth_gate,
)


def passing_input(**changes):
    base = AccountTruthGateInput(
        evaluated_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        code_revision="codex/ama-15-account-truth-acceptance",
        checks={name: CheckEvidence(True, (f"pytest:{name}",)) for name in REQUIRED_CHECKS},
        reconciliation_evidence_ids=("pytest:test_phase5_account_reconciliation.py",),
        unresolved_reconciliation_blockers=("TOSS_CASH_SOURCE_UNVERIFIABLE",),
        accepted_reconciliation_blockers=("TOSS_CASH_SOURCE_UNVERIFIABLE",),
        live_trading_enabled=False,
    )
    return replace(base, **changes)


def test_all_checks_with_explicitly_accepted_reconciliation_blocker_pass():
    result = evaluate_account_truth_gate(passing_input())
    assert result.decision is AcceptanceDecision.PASS
    assert result.reason_codes == ()
    assert result.blocker_ids == ()
    assert result.accepted_blocker_ids == ("TOSS_CASH_SOURCE_UNVERIFIABLE",)
    assert len(result.evidence_artifact_ids) == len(REQUIRED_CHECKS) + 1


@pytest.mark.parametrize("failed_check", REQUIRED_CHECKS)
def test_every_required_check_fails_closed(failed_check):
    inputs = passing_input()
    checks = dict(inputs.checks)
    checks[failed_check] = CheckEvidence(False, (f"pytest:{failed_check}",))
    result = evaluate_account_truth_gate(replace(inputs, checks=checks))
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == (f"CHECK_FAILED:{failed_check}",)


def test_unaccepted_reconciliation_blocker_and_live_write_fail():
    result = evaluate_account_truth_gate(passing_input(
        unresolved_reconciliation_blockers=("UNKNOWN_ORDER",),
        accepted_reconciliation_blockers=(),
        live_trading_enabled=True,
    ))
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == ("RECONCILIATION_BLOCKER_NOT_ACCEPTED", "LIVE_TRADING_ENABLED")
    assert result.blocker_ids == ("UNKNOWN_ORDER",)


def test_missing_unknown_or_conflicting_evidence_is_rejected():
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        passing_input(checks={})
    with pytest.raises(InvariantViolation, match="CHECK_UNKNOWN"):
        CheckEvidence(None, ("run",))
    with pytest.raises(InvariantViolation, match="EVIDENCE_INVALID"):
        CheckEvidence(True, ())
    with pytest.raises(InvariantViolation, match="LIVE_STATE_UNKNOWN"):
        passing_input(live_trading_enabled=None)
    with pytest.raises(InvariantViolation, match="ACCEPTED_BLOCKER_NOT_PRESENT"):
        passing_input(unresolved_reconciliation_blockers=())


def test_same_meaning_has_deterministic_result_and_schema_fields():
    first = evaluate_account_truth_gate(passing_input())
    inputs = passing_input(
        checks=dict(reversed(tuple(passing_input().checks.items()))),
    )
    second = evaluate_account_truth_gate(inputs)
    assert first == second
    schema_path = __import__("pathlib").Path(__file__).parents[1] / "schemas/account_truth_acceptance.schema.json"
    schema = json.loads(schema_path.read_text())
    assert set(schema["required"]) == set(asdict(first))


def test_recorded_gate_a_evidence_is_exact_reproducible_pass():
    root = __import__("pathlib").Path(__file__).parents[1]
    recorded = json.loads((root / "docs/evidence/gate_a_account_truth_2026-09-06.json").read_text())
    evidence = {
        "CASH_REPLAY_DETERMINISTIC": "pytest:tests/test_phase4_cash_position_settlement.py::test_cash_and_reservation_idempotency_conflicts_fail_closed",
        "POSITION_TAX_LOT_REPLAY_DETERMINISTIC": "pytest:tests/test_phase4_cash_position_settlement.py::test_position_settlement_sell_reservation_tax_lot_and_replay",
        "ORDER_EXECUTION_DELTA_DUPLICATE_SAFE": "pytest:tests/test_phase3_order_state_execution.py::test_repeated_cumulative_snapshot_creates_no_second_delta",
        "UNKNOWN_ORDER_STATE_BLOCKS_NEW_TRADES": "pytest:tests/test_phase3_order_state_execution.py::test_invalid_transition_and_unknown_state_fail_closed",
        "OPENING_AND_SETTLEMENT_EVIDENCE_VERIFIED": "pytest:tests/test_phase4_cash_position_settlement.py::test_execution_posting_fails_closed_on_missing_conflicting_or_malformed_settlement",
        "PORTFOLIO_ACCOUNTING_RECONCILED": "pytest:tests/test_phase4_portfolio_accounting.py::test_nav_contributions_currencies_and_external_flows_reconcile",
        "CLEAN_CHECKOUT_CI_BUILD_SECRET_SCAN_PASSED": "github-actions:33995216332",
    }
    inputs = AccountTruthGateInput(
        evaluated_at=datetime(2026, 9, 6, tzinfo=timezone.utc), code_revision="c2155a2",
        checks={name: CheckEvidence(True, (evidence[name],)) for name in REQUIRED_CHECKS},
        reconciliation_evidence_ids=("pytest:tests/test_phase5_account_reconciliation.py",),
        unresolved_reconciliation_blockers=("TOSS_CASH_SOURCE_UNVERIFIABLE",),
        accepted_reconciliation_blockers=("TOSS_CASH_SOURCE_UNVERIFIABLE",),
        live_trading_enabled=False,
    )
    actual = asdict(evaluate_account_truth_gate(inputs))
    actual["decision"] = actual["decision"].value
    for key in ("reason_codes", "blocker_ids", "accepted_blocker_ids", "evidence_artifact_ids"):
        actual[key] = list(actual[key])
    assert actual == recorded
