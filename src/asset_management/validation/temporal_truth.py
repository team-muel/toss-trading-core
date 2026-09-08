"""AMA-22 Temporal Truth acceptance gate."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Mapping

from asset_management.domain.errors import InvariantViolation
from .account_truth import AcceptanceDecision, CheckEvidence


REQUIRED_TEMPORAL_CHECKS = (
    "FUTURE_SENTINEL_ZERO_FAILURES",
    "AVAILABLE_AFTER_AS_OF_BLOCKED",
    "VINTAGE_REVISION_REPLAY_MATCHED",
    "SAME_DAY_CLOSE_LEAKAGE_BLOCKED",
    "PRE_RELEASE_AND_PRE_RECEIPT_ACCESS_BLOCKED",
    "DST_HOLIDAY_EARLY_CLOSE_PASSED",
    "POINT_IN_TIME_UNIVERSE_RESTORED",
    "CORPORATE_ACTION_PRICE_SEMANTICS_VERIFIED",
)


@dataclass(frozen=True, slots=True)
class TemporalTruthGateInput:
    evaluated_at: datetime
    code_revision: str
    checks: Mapping[str, CheckEvidence]

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise InvariantViolation("TEMPORAL_TRUTH_GATE_TIME_NOT_AWARE")
        if not self.code_revision.strip() or set(self.checks) != set(REQUIRED_TEMPORAL_CHECKS):
            raise InvariantViolation("TEMPORAL_TRUTH_GATE_CHECK_SET_INVALID")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))


@dataclass(frozen=True, slots=True)
class TemporalTruthGateResult:
    decision: AcceptanceDecision
    reason_codes: tuple[str, ...]
    evidence_artifact_ids: tuple[str, ...]
    evaluated_at: str
    code_revision: str
    content_hash: str


def evaluate_temporal_truth_gate(inputs: TemporalTruthGateInput) -> TemporalTruthGateResult:
    reasons = tuple(
        f"CHECK_FAILED:{name}" for name in REQUIRED_TEMPORAL_CHECKS
        if not inputs.checks[name].passed
    )
    decision = AcceptanceDecision.FAIL if reasons else AcceptanceDecision.PASS
    artifacts = tuple(sorted({artifact for check in inputs.checks.values()
                              for artifact in check.artifact_ids}))
    payload = {
        "decision": decision.value, "reason_codes": list(reasons),
        "evidence_artifact_ids": list(artifacts),
        "evaluated_at": inputs.evaluated_at.isoformat(),
        "code_revision": inputs.code_revision,
    }
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return TemporalTruthGateResult(decision, reasons, artifacts, inputs.evaluated_at.isoformat(),
                                   inputs.code_revision, digest)
