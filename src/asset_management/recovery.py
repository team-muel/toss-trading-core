"""Fail-closed crash-recovery evidence and RPO/RTO contracts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation


class RecoveryTier(StrEnum):
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    MICRO_LIVE = "MICRO_LIVE"


class RecoveryDomain(StrEnum):
    ACCOUNT_ORDER_FILL_LEDGER = "ACCOUNT_ORDER_FILL_LEDGER"
    DECISION_CHECKPOINT = "DECISION_CHECKPOINT"
    MARKET_RAW_DATA = "MARKET_RAW_DATA"
    DERIVED_FEATURE_MODEL_ARTIFACT = "DERIVED_FEATURE_MODEL_ARTIFACT"


class RecoveryState(StrEnum):
    READ_ONLY_RECONCILING = "READ_ONLY_RECONCILING"
    BLOCKED = "BLOCKED"
    READY_FOR_RUNTIME_REAUTHORIZATION = "READY_FOR_RUNTIME_REAUTHORIZATION"


def _text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvariantViolation(reason)
    return value


def _duration_map(values: object, reason: str) -> Mapping[RecoveryDomain, int]:
    if not isinstance(values, Mapping) or set(values) != set(RecoveryDomain):
        raise InvariantViolation(reason)
    result: dict[RecoveryDomain, int] = {}
    for key, value in values.items():
        if not isinstance(key, RecoveryDomain) or type(value) is not int or value < 0:
            raise InvariantViolation(reason)
        result[key] = value
    return MappingProxyType(dict(sorted(result.items(), key=lambda item: item[0].value)))


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    version: str
    tier: RecoveryTier
    max_rpo_seconds: Mapping[RecoveryDomain, int]
    max_rto_seconds: Mapping[RecoveryDomain, int]

    def __post_init__(self) -> None:
        _text(self.version, "RECOVERY_POLICY_INVALID")
        if not isinstance(self.tier, RecoveryTier):
            raise InvariantViolation("RECOVERY_POLICY_INVALID")
        rpo = _duration_map(self.max_rpo_seconds, "RECOVERY_POLICY_INVALID")
        rto = _duration_map(self.max_rto_seconds, "RECOVERY_POLICY_INVALID")
        if any(value == 0 for value in rto.values()):
            raise InvariantViolation("RECOVERY_POLICY_INVALID")
        object.__setattr__(self, "max_rpo_seconds", rpo)
        object.__setattr__(self, "max_rto_seconds", rto)


@dataclass(frozen=True, slots=True)
class RecoveryEvidence:
    recovered_at: datetime
    release_sha: str
    last_durable_checkpoint_id: str
    last_event_watermark: str
    broker_snapshot_id: str
    unresolved_order_ids: tuple[str, ...]
    replay_hash: str
    reconciliation_result_id: str
    reconciliation_clean: bool
    broker_snapshot_verified: bool
    replay_verified: bool
    actual_rpo_seconds: Mapping[RecoveryDomain, int]
    actual_rto_seconds: Mapping[RecoveryDomain, int]
    operator_approval_id: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.recovered_at, datetime) or self.recovered_at.tzinfo is None or
                self.recovered_at.utcoffset() is None or re.fullmatch(r"[0-9a-f]{7,64}", self.release_sha) is None):
            raise InvariantViolation("RECOVERY_EVIDENCE_INVALID")
        for value in (self.last_durable_checkpoint_id, self.last_event_watermark, self.broker_snapshot_id,
                      self.replay_hash, self.reconciliation_result_id):
            _text(value, "RECOVERY_EVIDENCE_INVALID")
        if (any(type(value) is not bool for value in (self.reconciliation_clean, self.broker_snapshot_verified,
                                                       self.replay_verified)) or
                not isinstance(self.unresolved_order_ids, tuple) or
                len(self.unresolved_order_ids) != len(set(self.unresolved_order_ids))):
            raise InvariantViolation("RECOVERY_EVIDENCE_INVALID")
        object.__setattr__(self, "unresolved_order_ids", tuple(sorted(_text(value, "RECOVERY_EVIDENCE_INVALID") for value in self.unresolved_order_ids)))
        if self.operator_approval_id is not None:
            _text(self.operator_approval_id, "RECOVERY_EVIDENCE_INVALID")
        object.__setattr__(self, "actual_rpo_seconds", _duration_map(self.actual_rpo_seconds, "RECOVERY_EVIDENCE_INVALID"))
        object.__setattr__(self, "actual_rto_seconds", _duration_map(self.actual_rto_seconds, "RECOVERY_EVIDENCE_INVALID"))
        object.__setattr__(self, "recovered_at", self.recovered_at.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    state: RecoveryState
    reason_codes: tuple[str, ...]
    recovery_mode: str
    evidence_hash: str

    @property
    def permits_runtime_reauthorization(self) -> bool:
        return self.state is RecoveryState.READY_FOR_RUNTIME_REAUTHORIZATION


def evaluate_recovery(policy: RecoveryPolicy, evidence: RecoveryEvidence) -> RecoveryDecision:
    if not isinstance(policy, RecoveryPolicy) or not isinstance(evidence, RecoveryEvidence):
        raise InvariantViolation("RECOVERY_INPUT_INVALID")
    reasons: list[str] = []
    for domain in RecoveryDomain:
        if evidence.actual_rpo_seconds[domain] > policy.max_rpo_seconds[domain]:
            reasons.append(f"RPO_EXCEEDED:{domain.value}")
        if evidence.actual_rto_seconds[domain] > policy.max_rto_seconds[domain]:
            reasons.append(f"RTO_EXCEEDED:{domain.value}")
    if not evidence.broker_snapshot_verified:
        reasons.append("BROKER_SNAPSHOT_UNVERIFIED")
    if not evidence.replay_verified:
        reasons.append("REPLAY_UNVERIFIED")
    if not evidence.reconciliation_clean:
        reasons.append("RECONCILIATION_INCOMPLETE")
    if reasons:
        state = RecoveryState.BLOCKED
    elif evidence.unresolved_order_ids:
        state = RecoveryState.READ_ONLY_RECONCILING
        reasons.append("UNRESOLVED_BROKER_ORDER")
    elif evidence.operator_approval_id is None:
        state = RecoveryState.READ_ONLY_RECONCILING
        reasons.append("OPERATOR_APPROVAL_REQUIRED")
    else:
        state = RecoveryState.READY_FOR_RUNTIME_REAUTHORIZATION
    body = {"policy_version": policy.version, "tier": policy.tier.value,
            "state": state.value, "reason_codes": reasons,
            "release_sha": evidence.release_sha, "checkpoint": evidence.last_durable_checkpoint_id,
            "watermark": evidence.last_event_watermark, "broker_snapshot": evidence.broker_snapshot_id,
            "unresolved_order_ids": list(evidence.unresolved_order_ids), "replay_hash": evidence.replay_hash,
            "reconciliation_result": evidence.reconciliation_result_id,
            "operator_approval_id": evidence.operator_approval_id,
            "actual_rpo_seconds": {key.value: value for key, value in evidence.actual_rpo_seconds.items()},
            "actual_rto_seconds": {key.value: value for key, value in evidence.actual_rto_seconds.items()}}
    return RecoveryDecision(state, tuple(reasons), "READ_ONLY", digest(canonical(body)))
