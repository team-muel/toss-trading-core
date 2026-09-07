"""AMA-61 Gate E: acceptance boundary for M5 portfolio decision integrity."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation

from .account_truth import AcceptanceDecision, CheckEvidence


REQUIRED_PORTFOLIO_DECISION_INTEGRITY_CHECKS = (
    "NAV_LIABILITY_CAPITAL_BASIS_NO_DOUBLE_COUNTING_VERIFIED",
    "ABSOLUTE_STRATEGIC_FORECAST_TOTAL_RETURN_OBJECTIVE_VERIFIED",
    "ACTIVE_BENCHMARK_FORECAST_TOTAL_RETURN_OBJECTIVE_VERIFIED",
    "MODEL_RELATIVE_ALPHA_MODE_AUTHORIZED_AND_FACTOR_NEUTRAL_VERIFIED",
    "PRICING_BASELINE_AND_COVARIANCE_PENALTY_SEPARATED",
    "CONSTRAINTS_AND_INFEASIBLE_FAIL_CLOSED",
    "COST_TAX_LIQUIDITY_NO_TRADE_UNIT_ALIGNMENT_VERIFIED",
    "GROSS_NET_TRANSACTION_TAX_DRAG_NOT_DOUBLE_COUNTED",
    "ORDER_AND_LIQUIDATION_CAPACITY_SEPARATED",
    "RISK_GOVERNOR_HARD_GATE_AND_FORECAST_AUTHORITY_ENFORCED",
    "TRANSITION_PREREQUISITES_VALIDITY_AND_COST_CURVE_VERIFIED",
    "TRANSITION_IMMEDIATE_STAGED_UTILITY_AND_REASSESSMENT_VERIFIED",
    "MANUAL_OVERRIDE_AUDIT_REPLAY_VERIFIED",
    "DECISION_RETURN_SEMANTICS_SEPARATED",
    "MANDATE_BENCHMARK_RISK_AUTHORITY_VERSION_PINNED",
    "OPTIMUM_STABILITY_PERTURBATION_VERIFIED",
    "DETERMINISTIC_DECISION_VERIFIED",
)


@dataclass(frozen=True, slots=True)
class PortfolioDecisionIntegrityGateInput:
    evaluated_at: datetime
    code_revision: str
    checks: Mapping[str, CheckEvidence]

    def __post_init__(self) -> None:
        if (not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None or
                self.evaluated_at.utcoffset() is None):
            raise InvariantViolation("PORTFOLIO_DECISION_INTEGRITY_GATE_TIME_NOT_AWARE")
        if (not isinstance(self.code_revision, str) or not self.code_revision.strip() or
                not isinstance(self.checks, Mapping) or
                set(self.checks) != set(REQUIRED_PORTFOLIO_DECISION_INTEGRITY_CHECKS) or
                any(not isinstance(check, CheckEvidence) for check in self.checks.values())):
            raise InvariantViolation("PORTFOLIO_DECISION_INTEGRITY_GATE_CHECK_SET_INVALID")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))


@dataclass(frozen=True, slots=True)
class PortfolioDecisionIntegrityGateResult:
    decision: AcceptanceDecision
    reason_codes: tuple[str, ...]
    evidence_artifact_ids: tuple[str, ...]
    evaluated_at: str
    code_revision: str
    content_hash: str

    @property
    def permits_m6_execution(self) -> bool:
        """M6 implementation remains blocked unless the complete Gate E record passes."""
        return self.decision is AcceptanceDecision.PASS


def evaluate_portfolio_decision_integrity_gate(
        inputs: PortfolioDecisionIntegrityGateInput) -> PortfolioDecisionIntegrityGateResult:
    reasons = tuple(f"CHECK_FAILED:{name}" for name in REQUIRED_PORTFOLIO_DECISION_INTEGRITY_CHECKS
                    if not inputs.checks[name].passed)
    decision = AcceptanceDecision.FAIL if reasons else AcceptanceDecision.PASS
    artifacts = tuple(sorted({artifact for check in inputs.checks.values() for artifact in check.artifact_ids}))
    payload = {"decision": decision.value, "reason_codes": list(reasons),
               "evidence_artifact_ids": list(artifacts), "evaluated_at": inputs.evaluated_at.isoformat(),
               "code_revision": inputs.code_revision}
    return PortfolioDecisionIntegrityGateResult(
        decision, reasons, artifacts, inputs.evaluated_at.isoformat(), inputs.code_revision,
        digest(canonical(payload)),
    )
