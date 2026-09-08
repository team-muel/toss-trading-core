"""AMA-39 Feature, State, and Model Integrity acceptance gate."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Mapping

from asset_management.domain.errors import InvariantViolation
from .account_truth import AcceptanceDecision, CheckEvidence


REQUIRED_FEATURE_STATE_MODEL_CHECKS = (
    "FEATURE_PIT_LEAKAGE_BLOCKED",
    "HORIZON_VALIDITY_DECAY_CONTRACT_VERIFIED",
    "FOUR_STATE_SNAPSHOTS_REPRODUCIBLE",
    "CALCULATION_LINEAGE_TO_RAW_MANIFEST_VERIFIED",
    "QUALITY_FRESHNESS_CONFIDENCE_PROPAGATION_VERIFIED",
    "MODEL_APPROVED_SCOPE_ENFORCED",
    "IDENTICAL_INPUT_VERSION_STATE_MODEL_OUTPUT_HASH_REPRODUCIBLE",
)


@dataclass(frozen=True, slots=True)
class FeatureStateModelIntegrityGateInput:
    evaluated_at: datetime
    code_revision: str
    checks: Mapping[str, CheckEvidence]

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise InvariantViolation("FEATURE_STATE_MODEL_GATE_TIME_NOT_AWARE")
        if (not self.code_revision.strip() or
                set(self.checks) != set(REQUIRED_FEATURE_STATE_MODEL_CHECKS)):
            raise InvariantViolation("FEATURE_STATE_MODEL_GATE_CHECK_SET_INVALID")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))


@dataclass(frozen=True, slots=True)
class FeatureStateModelIntegrityGateResult:
    decision: AcceptanceDecision
    reason_codes: tuple[str, ...]
    evidence_artifact_ids: tuple[str, ...]
    evaluated_at: str
    code_revision: str
    content_hash: str


def evaluate_feature_state_model_integrity_gate(
        inputs: FeatureStateModelIntegrityGateInput) -> FeatureStateModelIntegrityGateResult:
    reasons = tuple(
        f"CHECK_FAILED:{name}" for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS
        if not inputs.checks[name].passed
    )
    decision = AcceptanceDecision.FAIL if reasons else AcceptanceDecision.PASS
    artifacts = tuple(sorted({artifact for check in inputs.checks.values()
                              for artifact in check.artifact_ids}))
    payload = {
        "decision": decision.value,
        "reason_codes": list(reasons),
        "evidence_artifact_ids": list(artifacts),
        "evaluated_at": inputs.evaluated_at.isoformat(),
        "code_revision": inputs.code_revision,
    }
    content_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return FeatureStateModelIntegrityGateResult(
        decision, reasons, artifacts, inputs.evaluated_at.isoformat(), inputs.code_revision, content_hash,
    )
