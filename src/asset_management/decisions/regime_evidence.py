"""Typed boundary from causal regime evidence into RiskGovernor inputs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json

from asset_management.domain.errors import InvariantViolation
from asset_management.states import RegimeOutputSemantics, RegimeSnapshot

from .governor import RiskInputs


class RegimeUncertaintyReason(StrEnum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DIFFUSE_STATE_PROBABILITY = "DIFFUSE_STATE_PROBABILITY"
    HIGH_ENTROPY = "HIGH_ENTROPY"


@dataclass(frozen=True, slots=True)
class RegimeUncertaintyPolicy:
    policy_version: str
    minimum_confidence: Decimal
    minimum_dominant_probability: Decimal
    maximum_entropy: Decimal

    def __post_init__(self) -> None:
        values = (
            self.minimum_confidence,
            self.minimum_dominant_probability,
            self.maximum_entropy,
        )
        if (
            not isinstance(self.policy_version, str)
            or not self.policy_version.strip()
            or any(not isinstance(value, Decimal) or not value.is_finite() for value in values)
            or not Decimal(0) <= self.minimum_confidence <= Decimal(1)
            or not Decimal(0) <= self.minimum_dominant_probability <= Decimal(1)
            or self.maximum_entropy < 0
        ):
            raise InvariantViolation("REGIME_UNCERTAINTY_POLICY_INVALID")

    @property
    def content_hash(self) -> str:
        payload = {
            "policy_version": self.policy_version,
            "minimum_confidence": str(self.minimum_confidence),
            "minimum_dominant_probability": str(self.minimum_dominant_probability),
            "maximum_entropy": str(self.maximum_entropy),
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class RegimeUncertaintyEvidence:
    evidence_id: str
    regime_id: str
    source_state_id: str
    policy_version: str
    policy_hash: str
    evaluated_at: str
    uncertain: bool
    reason_codes: tuple[RegimeUncertaintyReason, ...]

    def __post_init__(self) -> None:
        hashes = (self.evidence_id, self.regime_id, self.source_state_id, self.policy_hash)
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in hashes
        ):
            raise InvariantViolation("REGIME_UNCERTAINTY_EVIDENCE_INVALID")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise InvariantViolation("REGIME_UNCERTAINTY_EVIDENCE_INVALID")
        try:
            evaluated = datetime.fromisoformat(self.evaluated_at)
        except (TypeError, ValueError) as exc:
            raise InvariantViolation("REGIME_UNCERTAINTY_EVIDENCE_INVALID") from exc
        if evaluated.tzinfo is None or evaluated.utcoffset() is None:
            raise InvariantViolation("REGIME_UNCERTAINTY_EVIDENCE_INVALID")
        if type(self.uncertain) is not bool:
            raise InvariantViolation("REGIME_UNCERTAINTY_EVIDENCE_INVALID")
        if (
            len(self.reason_codes) != len(set(self.reason_codes))
            or any(not isinstance(reason, RegimeUncertaintyReason) for reason in self.reason_codes)
            or self.uncertain != bool(self.reason_codes)
        ):
            raise InvariantViolation("REGIME_UNCERTAINTY_EVIDENCE_INVALID")
        evaluated_utc = evaluated.astimezone(timezone.utc)
        ordered_reasons = tuple(sorted(self.reason_codes, key=lambda reason: reason.value))
        object.__setattr__(self, "evaluated_at", evaluated_utc.isoformat())
        object.__setattr__(self, "reason_codes", ordered_reasons)
        body = {
            "regime_id": self.regime_id,
            "source_state_id": self.source_state_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "evaluated_at": evaluated_utc.isoformat(),
            "uncertain": self.uncertain,
            "reason_codes": [reason.value for reason in ordered_reasons],
        }
        expected_id = sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if self.evidence_id != expected_id:
            raise InvariantViolation("REGIME_UNCERTAINTY_EVIDENCE_IDENTITY_MISMATCH")

    def payload(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "regime_id": self.regime_id,
            "source_state_id": self.source_state_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "evaluated_at": self.evaluated_at,
            "uncertain": self.uncertain,
            "reason_codes": [reason.value for reason in self.reason_codes],
        }


def evaluate_regime_uncertainty(
    snapshot: RegimeSnapshot,
    policy: RegimeUncertaintyPolicy,
    *,
    evaluated_at: datetime,
) -> RegimeUncertaintyEvidence:
    if not isinstance(snapshot, RegimeSnapshot):
        raise InvariantViolation("REGIME_SNAPSHOT_REQUIRED")
    if not isinstance(policy, RegimeUncertaintyPolicy):
        raise InvariantViolation("REGIME_UNCERTAINTY_POLICY_INVALID")
    if snapshot.output_semantics is not RegimeOutputSemantics.FILTERED_CAUSAL:
        raise InvariantViolation("RETROSPECTIVE_REGIME_CANNOT_ENTER_RISK")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise InvariantViolation("REGIME_UNCERTAINTY_TIME_INVALID")
    evaluated = evaluated_at.astimezone(timezone.utc)
    snapshot_as_of = datetime.fromisoformat(snapshot.as_of).astimezone(timezone.utc)
    if evaluated < snapshot_as_of:
        raise InvariantViolation("REGIME_UNCERTAINTY_EVALUATED_BEFORE_SNAPSHOT")

    dominant_probability = max(item.probability for item in snapshot.state_probabilities)
    reasons: list[RegimeUncertaintyReason] = []
    if snapshot.confidence < policy.minimum_confidence:
        reasons.append(RegimeUncertaintyReason.LOW_CONFIDENCE)
    if dominant_probability < policy.minimum_dominant_probability:
        reasons.append(RegimeUncertaintyReason.DIFFUSE_STATE_PROBABILITY)
    if snapshot.entropy > policy.maximum_entropy:
        reasons.append(RegimeUncertaintyReason.HIGH_ENTROPY)

    body = {
        "regime_id": snapshot.regime_id,
        "source_state_id": snapshot.source_state_id,
        "policy_version": policy.policy_version,
        "policy_hash": policy.content_hash,
        "evaluated_at": evaluated.isoformat(),
        "uncertain": bool(reasons),
        "reason_codes": sorted(reason.value for reason in reasons),
    }
    evidence_id = sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return RegimeUncertaintyEvidence(
        evidence_id=evidence_id,
        regime_id=snapshot.regime_id,
        source_state_id=snapshot.source_state_id,
        policy_version=policy.policy_version,
        policy_hash=policy.content_hash,
        evaluated_at=evaluated.isoformat(),
        uncertain=bool(reasons),
        reason_codes=tuple(reasons),
    )


def bind_regime_uncertainty(
    inputs: RiskInputs,
    evidence: RegimeUncertaintyEvidence,
) -> RiskInputs:
    if not isinstance(inputs, RiskInputs):
        raise InvariantViolation("RISK_INPUTS_REQUIRED")
    if not isinstance(evidence, RegimeUncertaintyEvidence):
        raise InvariantViolation("REGIME_UNCERTAINTY_EVIDENCE_REQUIRED")
    try:
        risk_as_of = datetime.fromisoformat(inputs.as_of_utc)
        evidence_at = datetime.fromisoformat(evidence.evaluated_at)
    except (TypeError, ValueError) as exc:
        raise InvariantViolation("REGIME_UNCERTAINTY_TIME_INVALID") from exc
    if (risk_as_of.tzinfo is None or risk_as_of.utcoffset() is None or
            evidence_at.tzinfo is None or evidence_at.utcoffset() is None):
        raise InvariantViolation("REGIME_UNCERTAINTY_TIME_INVALID")
    if evidence_at.astimezone(timezone.utc) > risk_as_of.astimezone(timezone.utc):
        raise InvariantViolation("FUTURE_REGIME_EVIDENCE_FORBIDDEN")
    evidence_ids = tuple(sorted(set(inputs.evidence_ids) | {evidence.evidence_id}))
    return replace(
        inputs,
        evidence_ids=evidence_ids,
        regime_uncertain=evidence.uncertain,
        regime_uncertainty_evidence_id=evidence.evidence_id,
    )
