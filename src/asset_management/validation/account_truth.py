"""AMA-15 Account Truth acceptance gate with immutable evidence binding."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Mapping

from asset_management.domain.errors import InvariantViolation


REQUIRED_CHECKS = (
    "CASH_REPLAY_DETERMINISTIC",
    "POSITION_TAX_LOT_REPLAY_DETERMINISTIC",
    "ORDER_EXECUTION_DELTA_DUPLICATE_SAFE",
    "UNKNOWN_ORDER_STATE_BLOCKS_NEW_TRADES",
    "OPENING_AND_SETTLEMENT_EVIDENCE_VERIFIED",
    "PORTFOLIO_ACCOUNTING_RECONCILED",
    "CLEAN_CHECKOUT_CI_BUILD_SECRET_SCAN_PASSED",
)


class AcceptanceDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class CheckEvidence:
    passed: bool
    artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.passed) is not bool:
            raise InvariantViolation("ACCOUNT_TRUTH_GATE_CHECK_UNKNOWN")
        if (not self.artifact_ids or any(not value.strip() for value in self.artifact_ids)
                or len(set(self.artifact_ids)) != len(self.artifact_ids)):
            raise InvariantViolation("ACCOUNT_TRUTH_GATE_EVIDENCE_INVALID")
        object.__setattr__(self, "artifact_ids", tuple(sorted(self.artifact_ids)))


@dataclass(frozen=True, slots=True)
class AccountTruthGateInput:
    evaluated_at: datetime
    code_revision: str
    checks: Mapping[str, CheckEvidence]
    reconciliation_evidence_ids: tuple[str, ...]
    unresolved_reconciliation_blockers: tuple[str, ...] = ()
    accepted_reconciliation_blockers: tuple[str, ...] = ()
    live_trading_enabled: bool = False

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise InvariantViolation("ACCOUNT_TRUTH_GATE_TIME_NOT_AWARE")
        if not self.code_revision.strip() or set(self.checks) != set(REQUIRED_CHECKS):
            raise InvariantViolation("ACCOUNT_TRUTH_GATE_CHECK_SET_INVALID")
        if (not self.reconciliation_evidence_ids or
                any(not value.strip() for value in self.reconciliation_evidence_ids)):
            raise InvariantViolation("ACCOUNT_TRUTH_GATE_RECONCILIATION_EVIDENCE_MISSING")
        for values in (self.reconciliation_evidence_ids, self.unresolved_reconciliation_blockers,
                       self.accepted_reconciliation_blockers):
            if any(not value.strip() for value in values) or len(set(values)) != len(values):
                raise InvariantViolation("ACCOUNT_TRUTH_GATE_IDENTIFIER_INVALID")
        if not set(self.accepted_reconciliation_blockers) <= set(self.unresolved_reconciliation_blockers):
            raise InvariantViolation("ACCOUNT_TRUTH_GATE_ACCEPTED_BLOCKER_NOT_PRESENT")
        if type(self.live_trading_enabled) is not bool:
            raise InvariantViolation("ACCOUNT_TRUTH_GATE_LIVE_STATE_UNKNOWN")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))
        for name in ("reconciliation_evidence_ids", "unresolved_reconciliation_blockers",
                     "accepted_reconciliation_blockers"):
            object.__setattr__(self, name, tuple(sorted(getattr(self, name))))


@dataclass(frozen=True, slots=True)
class AccountTruthGateResult:
    decision: AcceptanceDecision
    reason_codes: tuple[str, ...]
    blocker_ids: tuple[str, ...]
    accepted_blocker_ids: tuple[str, ...]
    evidence_artifact_ids: tuple[str, ...]
    evaluated_at: str
    code_revision: str
    content_hash: str


def evaluate_account_truth_gate(inputs: AccountTruthGateInput) -> AccountTruthGateResult:
    reasons = [f"CHECK_FAILED:{name}" for name in REQUIRED_CHECKS if not inputs.checks[name].passed]
    unresolved = set(inputs.unresolved_reconciliation_blockers)
    accepted = set(inputs.accepted_reconciliation_blockers)
    unaccepted = tuple(sorted(unresolved - accepted))
    if unaccepted:
        reasons.append("RECONCILIATION_BLOCKER_NOT_ACCEPTED")
    if inputs.live_trading_enabled:
        reasons.append("LIVE_TRADING_ENABLED")
    decision = AcceptanceDecision.FAIL if reasons else AcceptanceDecision.PASS
    artifacts = set(inputs.reconciliation_evidence_ids)
    for check in inputs.checks.values():
        artifacts.update(check.artifact_ids)
    payload = {
        "decision": decision.value,
        "reason_codes": reasons,
        "blocker_ids": list(unaccepted),
        "accepted_blocker_ids": sorted(unresolved & accepted),
        "evidence_artifact_ids": sorted(artifacts),
        "evaluated_at": inputs.evaluated_at.isoformat(),
        "code_revision": inputs.code_revision,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = sha256(encoded.encode()).hexdigest()
    return AccountTruthGateResult(
        decision, tuple(reasons), unaccepted, tuple(sorted(unresolved & accepted)), tuple(sorted(artifacts)),
        inputs.evaluated_at.isoformat(), inputs.code_revision, digest,
    )
