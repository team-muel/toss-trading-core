from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.recovery import (RecoveryDomain, RecoveryEvidence, RecoveryPolicy, RecoveryState,
                                       RecoveryTier, evaluate_recovery)


def limits(value):
    return {domain: value for domain in RecoveryDomain}


def policy():
    return RecoveryPolicy("recovery@1", RecoveryTier.PAPER, limits(0), limits(300))


def evidence(**changes):
    values = dict(recovered_at=datetime(2026, 9, 7, tzinfo=timezone.utc), release_sha="1d62f8d",
                  last_durable_checkpoint_id="checkpoint:9", last_event_watermark="event:44",
                  broker_snapshot_id="broker:44", unresolved_order_ids=(), replay_hash="a" * 64,
                  reconciliation_result_id="reconciliation:44", reconciliation_clean=True,
                  broker_snapshot_verified=True, replay_verified=True, actual_rpo_seconds=limits(0),
                  actual_rto_seconds=limits(30), operator_approval_id="approval:44")
    values.update(changes)
    return RecoveryEvidence(**values)


def test_recovery_requires_durable_evidence_and_never_restores_prior_runtime_mode_automatically():
    decision = evaluate_recovery(policy(), evidence())
    assert decision.state is RecoveryState.READY_FOR_RUNTIME_REAUTHORIZATION
    assert decision.recovery_mode == "READ_ONLY" and decision.permits_runtime_reauthorization


@pytest.mark.parametrize("changes, reason", [
    ({"unresolved_order_ids": ("order:1",)}, "UNRESOLVED_BROKER_ORDER"),
    ({"operator_approval_id": None}, "OPERATOR_APPROVAL_REQUIRED"),
    ({"reconciliation_clean": False}, "RECONCILIATION_INCOMPLETE"),
    ({"replay_verified": False}, "REPLAY_UNVERIFIED"),
    ({"actual_rpo_seconds": limits(1)}, "RPO_EXCEEDED:ACCOUNT_ORDER_FILL_LEDGER"),
    ({"actual_rto_seconds": limits(301)}, "RTO_EXCEEDED:ACCOUNT_ORDER_FILL_LEDGER"),
])
def test_ambiguous_or_incomplete_recovery_remains_read_only_or_blocks(changes, reason):
    result = evaluate_recovery(policy(), evidence(**changes))
    assert reason in result.reason_codes and not result.permits_runtime_reauthorization
    assert result.recovery_mode == "READ_ONLY"


def test_missing_and_conflicting_recovery_inputs_fail_closed():
    with pytest.raises(InvariantViolation, match="EVIDENCE_INVALID"):
        evidence(release_sha="not-a-sha")
    with pytest.raises(InvariantViolation, match="POLICY_INVALID"):
        RecoveryPolicy("", RecoveryTier.PAPER, limits(0), limits(300))
    with pytest.raises(InvariantViolation, match="EVIDENCE_INVALID"):
        evidence(unresolved_order_ids=("order:1", "order:1"))


def test_schema_covers_the_serialized_recovery_decision():
    schema = json.loads((Path(__file__).parents[1] / "schemas/crash_recovery.schema.json").read_text())
    assert set(schema["required"]) == set(evaluate_recovery(policy(), evidence()).__dataclass_fields__)
