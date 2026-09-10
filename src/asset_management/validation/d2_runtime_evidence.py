"""Runtime evidence assembly for the three initially blocked Gate D2 checks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from asset_management.calculations import (MODEL_LINEAGE_EVIDENCE_DATASET, CalculationLineageGraph,
                                           ModelCalculationBinding, bind_authorized_model_calculation,
                                           model_authorization_payload)
from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.economics import CurrencyBasis
from asset_management.governance import ModelAuthorization, ModelRegistry
from asset_management.pricing import RiskFreeCurve, materialize_usd_fred_risk_free_curve
from asset_management.risk import (FACTOR_RISK_EVIDENCE_DATASET, FactorRiskAssessment,
                                   SpecificRiskPolicy, specific_risk_policy_payload)
from asset_management.risk.covariance import is_psd

from .account_truth import CheckEvidence
from .pricing_expectation_risk_integrity import (
    REQUIRED_PRICING_EXPECTATION_RISK_CHECKS,
    PricingExpectationRiskIntegrityGateInput,
)


_MANIFEST = re.compile(r"[0-9a-f]{64}")


def _manifest_evidence(ids: tuple[str, ...], *, reason: str) -> tuple[str, ...]:
    if not ids or len(ids) != len(set(ids)) or any(_MANIFEST.fullmatch(value) is None for value in ids):
        raise ValueError(reason)
    return tuple(f"dataset-manifest:{value}" for value in sorted(ids))


def _aware(value: datetime, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(reason)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class RiskFreeRuntimeEvidence:
    curve: RiskFreeCurve
    currency: str
    information_cutoff: datetime
    store: ImmutableDatasetStore

    def check(self) -> CheckEvidence:
        cutoff = _aware(self.information_cutoff, "RISK_FREE_CUTOFF_INVALID")
        if self.currency != "USD":
            raise ValueError("RISK_FREE_CURRENCY_NOT_APPROVED")
        returns = tuple(self.curve.return_for(currency=self.currency, horizon=horizon,
                                              information_cutoff=cutoff)
                        for horizon in (21, 63, 126, 252))
        if any(item.source != "fred-alfred" for item in returns):
            raise ValueError("RISK_FREE_SOURCE_NOT_APPROVED")
        manifests = _manifest_evidence(
            tuple(sorted({item.dataset_manifest_id for item in returns})), reason="RISK_FREE_MANIFEST_INVALID")
        manifest_id = returns[0].dataset_manifest_id
        manifest, _ = self.store.read(manifest_id)
        available_at = _aware(datetime.fromisoformat(manifest.available_at), "RISK_FREE_MANIFEST_TIME_INVALID")
        if (manifest.layer != "bronze" or manifest.source != "fred-alfred" or
                manifest.dataset != "risk-free-curve" or
                manifest.schema_version != "fred-risk-free-curve@1" or
                manifest.quality_status != "RAW" or available_at > cutoff):
            raise ValueError("RISK_FREE_MANIFEST_CONTEXT_INVALID")
        rebuilt = materialize_usd_fred_risk_free_curve(
            store=self.store, manifest_id=manifest_id, information_cutoff=cutoff)
        for horizon, supplied in zip((21, 63, 126, 252), returns):
            actual = rebuilt.return_for(currency=self.currency, horizon=horizon, information_cutoff=cutoff)
            if supplied != actual:
                raise ValueError("RISK_FREE_VALUES_NOT_BOUND_TO_ARTIFACT")
        return CheckEvidence(True, manifests)


@dataclass(frozen=True, slots=True)
class FactorRiskRuntimeEvidence:
    assessment: FactorRiskAssessment
    specific_risk_policy: SpecificRiskPolicy
    information_cutoff: datetime
    evidence_manifest_id: str
    store: ImmutableDatasetStore

    def check(self) -> CheckEvidence:
        cutoff = _aware(self.information_cutoff, "FACTOR_RISK_CUTOFF_INVALID")
        if (self.assessment.currency_basis is not CurrencyBasis.BASE or
                self.assessment.as_of > cutoff):
            raise ValueError("FACTOR_RISK_CONTEXT_INVALID")
        if (not is_psd(self.assessment.covariance) or
                any(value < self.specific_risk_policy.residual_variance_floor
                    for value in self.assessment.specific_variance)):
            raise ValueError("FACTOR_RISK_DECOMPOSITION_INVALID")
        manifests = _manifest_evidence((self.evidence_manifest_id,), reason="FACTOR_RISK_MANIFEST_INVALID")
        evidence_manifest, body = self.store.read(self.evidence_manifest_id)
        evidence_available_at = _aware(
            datetime.fromisoformat(evidence_manifest.available_at), "FACTOR_RISK_MANIFEST_TIME_INVALID")
        if self.assessment.estimation_version != self.specific_risk_policy.estimation_version:
            raise ValueError("FACTOR_RISK_POLICY_VERSION_MISMATCH")
        if (evidence_manifest.layer != "gold" or evidence_manifest.source != "tiingo-eod" or
                evidence_manifest.dataset != FACTOR_RISK_EVIDENCE_DATASET or evidence_available_at > cutoff):
            raise ValueError("FACTOR_RISK_EVIDENCE_BINDING_INVALID")
        # Metadata or a stored copy of a caller's assessment does not prove the
        # covariance/residual estimates came from the declared return inputs.
        # No canonical raw-return estimator/replay contract is connected here
        # yet. Old v1 receipts and new mechanism-only v2 receipts both stay
        # blocked rather than allowing an arbitrary Tiingo artifact to pass D2.
        raise ValueError("FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED")


@dataclass(frozen=True, slots=True)
class ModelLineageRuntimeEvidence:
    registry: ModelRegistry
    authorization: ModelAuthorization
    binding: ModelCalculationBinding
    lineage: CalculationLineageGraph
    store: ImmutableDatasetStore
    information_cutoff: datetime
    evidence_manifest_id: str

    def check(self) -> CheckEvidence:
        cutoff = _aware(self.information_cutoff, "MODEL_LINEAGE_CUTOFF_INVALID")
        self.registry.require_authorization(self.authorization, model_key=self.binding.model_key,
                                            scope=self.binding.scope, at=cutoff)
        if (self.binding.authorization_hash != self.authorization.authorization_hash or
                self.binding.lineage_graph_hash != self.lineage.graph_hash or
                self.binding.final_node_id != self.lineage.final_node_id or
                self.binding.bound_at > cutoff):
            raise ValueError("MODEL_LINEAGE_BINDING_INVALID")
        # Verify persisted/reconstructed bindings through their owner as well
        # as their hashes. Publication alone is not an admission authority.
        rebuilt = bind_authorized_model_calculation(
            model_registry=self.registry, authorization=self.authorization,
            model_key=self.binding.model_key, scope=self.binding.scope,
            lineage=self.lineage, bound_at=self.binding.bound_at)
        if rebuilt != self.binding:
            raise ValueError("MODEL_LINEAGE_BINDING_INVALID")
        self.lineage.verify_raw_manifests(self.store)
        raw_manifests = tuple(node.raw_manifest_id for node in self.lineage.trace()
                              if node.raw_manifest_id is not None)
        binding_at = _aware(self.binding.bound_at, "MODEL_LINEAGE_BINDING_TIME_INVALID")
        for manifest_id in raw_manifests:
            raw_manifest, _ = self.store.read(manifest_id)
            raw_available_at = _aware(datetime.fromisoformat(raw_manifest.available_at),
                                      "MODEL_LINEAGE_RAW_MANIFEST_TIME_INVALID")
            if raw_available_at > binding_at:
                raise ValueError("MODEL_LINEAGE_RAW_AFTER_BINDING")
        evidence, body = self.store.read(self.evidence_manifest_id)
        evidence_available_at = _aware(datetime.fromisoformat(evidence.available_at),
                                        "MODEL_LINEAGE_MANIFEST_TIME_INVALID")
        if (evidence.layer != "gold" or evidence.source != "model-lineage" or
                evidence.dataset != MODEL_LINEAGE_EVIDENCE_DATASET or
                evidence.schema_version != "model-lineage-evidence@1" or
                evidence.quality_status != "VALID" or evidence_available_at > cutoff or
                evidence.parent_manifest_ids != tuple(sorted(raw_manifests)) or not isinstance(body, dict) or
                body != {"registry": self.registry.payload(),
                         "model_authorization": model_authorization_payload(self.authorization),
                         "binding": self.binding.payload(), "lineage": self.lineage.payload()}):
            raise ValueError("MODEL_LINEAGE_EVIDENCE_BINDING_INVALID")
        return CheckEvidence(True, _manifest_evidence((self.evidence_manifest_id,),
            reason="MODEL_LINEAGE_MANIFEST_INVALID") + _manifest_evidence(
                raw_manifests, reason="MODEL_LINEAGE_MANIFEST_INVALID"))


@dataclass(frozen=True, slots=True)
class D2RuntimeEvidenceResult:
    checks: dict[str, CheckEvidence]
    failure_reasons: dict[str, str]


RUNTIME_D2_CHECKS = frozenset((
    "RISK_FREE_CURRENCY_HORIZON_COMPOUNDING_VERIFIED",
    "FACTOR_SPECIFIC_RISK_DECOMPOSITION_AND_FLOOR_VERIFIED",
    "MODEL_SCOPE_AND_CALCULATION_LINEAGE_COMPLETE",
))


def assemble_d2_runtime_evidence(*, risk_free: RiskFreeRuntimeEvidence | None,
                                 factor_risk: FactorRiskRuntimeEvidence | None,
                                 model_lineage: ModelLineageRuntimeEvidence | None) -> D2RuntimeEvidenceResult:
    """Build the initially blocked D2 checks from artifacts, never caller booleans."""
    sources = {
        "RISK_FREE_CURRENCY_HORIZON_COMPOUNDING_VERIFIED": risk_free,
        "FACTOR_SPECIFIC_RISK_DECOMPOSITION_AND_FLOOR_VERIFIED": factor_risk,
        "MODEL_SCOPE_AND_CALCULATION_LINEAGE_COMPLETE": model_lineage,
    }
    checks: dict[str, CheckEvidence] = {}
    failures: dict[str, str] = {}
    for check_name, source in sources.items():
        if source is None:
            failures[check_name] = "EVIDENCE_SOURCE_MISSING"
            continue
        try:
            checks[check_name] = source.check()
        except Exception as exc:  # fail closed and preserve the specific rejection reason
            failures[check_name] = str(exc) or exc.__class__.__name__
    return D2RuntimeEvidenceResult(checks, failures)


def build_d2_gate_input(*, static_checks: dict[str, CheckEvidence],
                        risk_free: RiskFreeRuntimeEvidence | None,
                        factor_risk: FactorRiskRuntimeEvidence | None,
                        model_lineage: ModelLineageRuntimeEvidence | None) -> PricingExpectationRiskIntegrityGateInput:
    """Combine caller-supplied static evidence with artifact-derived runtime checks."""
    overlap = RUNTIME_D2_CHECKS.intersection(static_checks)
    if overlap:
        raise ValueError("D2_RUNTIME_CHECKS_CALLER_SUPPLIED")
    result = assemble_d2_runtime_evidence(
        risk_free=risk_free, factor_risk=factor_risk, model_lineage=model_lineage)
    checks = dict(static_checks)
    checks.update(result.checks)
    if set(checks) != set(REQUIRED_PRICING_EXPECTATION_RISK_CHECKS):
        missing = sorted(set(REQUIRED_PRICING_EXPECTATION_RISK_CHECKS).difference(checks))
        raise ValueError(f"D2_EVIDENCE_INCOMPLETE:{','.join(missing)}")
    return PricingExpectationRiskIntegrityGateInput(checks)
