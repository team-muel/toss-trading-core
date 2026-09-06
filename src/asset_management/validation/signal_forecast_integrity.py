"""AMA-108 Signal and Forecast Integrity acceptance gate."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation
from .account_truth import AcceptanceDecision, CheckEvidence


REQUIRED_SIGNAL_FORECAST_CHECKS = (
    "FEATURE_SIGNAL_SEMANTIC_SEPARATION_VERIFIED",
    "SIGNAL_CONTRACT_LINEAGE_HORIZON_VALIDITY_VERIFIED",
    "CROSS_SECTIONAL_SIGNAL_DIAGNOSTICS_VERIFIED",
    "ETF_TIME_SERIES_OOS_CALIBRATION_UTILITY_VERIFIED",
    "PIT_NORMALIZATION_NEUTRALIZATION_VERIFIED",
    "SIGNAL_REDUNDANCY_FACTOR_INCREMENTAL_POWER_VERIFIED",
    "SIGNAL_FORECAST_OOS_UNCERTAINTY_LINEAGE_VERIFIED",
    "FORECAST_COMBINATION_DIVERSIFICATION_COST_STABILITY_VERIFIED",
    "STRATEGY_VERSION_REFERENCES_VERIFIED",
    "WEAK_SIGNAL_SHRINK_OR_ABSTAIN_VERIFIED",
    "LOOK_AHEAD_SURVIVORSHIP_LEAKAGE_BLOCKED",
)


@dataclass(frozen=True, slots=True)
class SignalForecastIntegrityGateInput:
    evaluated_at: datetime
    code_revision: str
    checks: Mapping[str, CheckEvidence]

    def __post_init__(self) -> None:
        if (not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None or
                self.evaluated_at.utcoffset() is None):
            raise InvariantViolation("SIGNAL_FORECAST_GATE_TIME_NOT_AWARE")
        if (not isinstance(self.code_revision, str) or not self.code_revision.strip() or
                not isinstance(self.checks, Mapping) or
                set(self.checks) != set(REQUIRED_SIGNAL_FORECAST_CHECKS) or
                any(not isinstance(check, CheckEvidence) for check in self.checks.values())):
            raise InvariantViolation("SIGNAL_FORECAST_GATE_CHECK_SET_INVALID")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))


@dataclass(frozen=True, slots=True)
class SignalForecastIntegrityGateResult:
    decision: AcceptanceDecision
    reason_codes: tuple[str, ...]
    evidence_artifact_ids: tuple[str, ...]
    evaluated_at: str
    code_revision: str
    content_hash: str

    @property
    def permits_m4_execution(self) -> bool:
        """Only a complete signal-and-forecast evidence set can cross into M4."""
        return self.decision is AcceptanceDecision.PASS


def evaluate_signal_forecast_integrity_gate(
        inputs: SignalForecastIntegrityGateInput) -> SignalForecastIntegrityGateResult:
    reasons = tuple(
        f"CHECK_FAILED:{name}" for name in REQUIRED_SIGNAL_FORECAST_CHECKS
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
    return SignalForecastIntegrityGateResult(
        decision, reasons, artifacts, inputs.evaluated_at.isoformat(), inputs.code_revision,
        digest(canonical(payload)),
    )
