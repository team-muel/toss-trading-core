"""AMA-50 Gate D2: pricing, expectation, and risk acceptance boundary."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation

from .account_truth import AcceptanceDecision, CheckEvidence


REQUIRED_PRICING_EXPECTATION_RISK_CHECKS = (
    "AMA_100_ECONOMIC_SEMANTICS_VERIFIED",
    "AMA_101_SEMANTIC_REMEDIATION_VERIFIED",
    "RISK_FREE_CURRENCY_HORIZON_COMPOUNDING_VERIFIED",
    "EQUITY_PRICING_BASELINE_SCOPE_AND_ASSET_APPLICABILITY_VERIFIED",
    "RETURN_SEMANTIC_TYPES_SEPARATED",
    "EXPECTED_RETURN_COMPONENTS_AND_PER_SHARE_GROWTH_RECONCILED",
    "NON_EQUITY_FORECASTS_INDEPENDENT_OF_PRICING_BASELINE_VERIFIED",
    "UNCERTAINTY_SHRINKAGE_ABSTAIN_AND_NO_DOUBLE_CONFIDENCE_VERIFIED",
    "COVARIANCE_PSD_AND_STABILITY_VERIFIED",
    "FACTOR_SPECIFIC_RISK_DECOMPOSITION_AND_FLOOR_VERIFIED",
    "VARIANCE_CONTRIBUTIONS_RECONCILE",
    "VOLATILITY_CONTRIBUTIONS_RECONCILE",
    "VAR_CVAR_FX_LIQUIDITY_STRESS_REPRODUCIBLE",
    "NUMERIC_CURRENCY_HORIZON_UNIT_FORMULA_CONTEXT_COMPLETE",
    "MANDATE_BENCHMARK_RISK_AUTHORITY_PRE_REGISTERED",
    "MODEL_SCOPE_AND_CALCULATION_LINEAGE_COMPLETE",
)


@dataclass(frozen=True, slots=True)
class PricingExpectationRiskIntegrityGateInput:
    evaluated_at: datetime
    code_revision: str
    checks: Mapping[str, CheckEvidence]

    def __post_init__(self) -> None:
        if (not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None or
                self.evaluated_at.utcoffset() is None):
            raise InvariantViolation("PRICING_EXPECTATION_RISK_GATE_TIME_NOT_AWARE")
        if (not isinstance(self.code_revision, str) or not self.code_revision.strip() or
                not isinstance(self.checks, Mapping) or
                set(self.checks) != set(REQUIRED_PRICING_EXPECTATION_RISK_CHECKS) or
                any(not isinstance(value, CheckEvidence) for value in self.checks.values())):
            raise InvariantViolation("PRICING_EXPECTATION_RISK_GATE_CHECK_SET_INVALID")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))


@dataclass(frozen=True, slots=True)
class PricingExpectationRiskIntegrityGateResult:
    decision: AcceptanceDecision
    reason_codes: tuple[str, ...]
    evidence_artifact_ids: tuple[str, ...]
    evaluated_at: str
    code_revision: str
    content_hash: str

    @property
    def permits_m5_execution(self) -> bool:
        return self.decision is AcceptanceDecision.PASS


def evaluate_pricing_expectation_risk_integrity_gate(
        inputs: PricingExpectationRiskIntegrityGateInput) -> PricingExpectationRiskIntegrityGateResult:
    reasons = tuple(f"CHECK_FAILED:{name}" for name in REQUIRED_PRICING_EXPECTATION_RISK_CHECKS
                    if not inputs.checks[name].passed)
    decision = AcceptanceDecision.FAIL if reasons else AcceptanceDecision.PASS
    artifacts = tuple(sorted({artifact for check in inputs.checks.values() for artifact in check.artifact_ids}))
    payload = {"decision": decision.value, "reason_codes": list(reasons),
               "evidence_artifact_ids": list(artifacts), "evaluated_at": inputs.evaluated_at.isoformat(),
               "code_revision": inputs.code_revision}
    return PricingExpectationRiskIntegrityGateResult(
        decision, reasons, artifacts, inputs.evaluated_at.isoformat(), inputs.code_revision,
        digest(canonical(payload)),
    )
