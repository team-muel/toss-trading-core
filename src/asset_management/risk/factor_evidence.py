"""Immutable factor/specific-risk evidence publication for Gate D2."""

from __future__ import annotations

from datetime import datetime, timezone

from asset_management.data.immutable import ImmutableDatasetStore, StoredDatasetManifest, canonical, digest
from asset_management.domain.errors import DataQualityError

from .factor_risk import FactorRiskAssessment, SpecificRiskPolicy


FACTOR_RISK_EVIDENCE_DATASET = "factor-specific-risk-evidence"


def specific_risk_policy_payload(policy: SpecificRiskPolicy) -> dict[str, object]:
    return {
        "minimum_history": policy.minimum_history,
        "residual_variance_floor": str(policy.residual_variance_floor),
        "shrinkage_weight": str(policy.shrinkage_weight),
        "winsorization_limit": str(policy.winsorization_limit),
        "estimation_version": policy.estimation_version,
    }


def publish_factor_risk_evidence(*, store: ImmutableDatasetStore, assessment: FactorRiskAssessment,
                                 policy: SpecificRiskPolicy, source_manifest_ids: tuple[str, ...],
                                 published_at: datetime, code_revision: str) -> StoredDatasetManifest:
    """Publish a factor assessment only when its Tiingo inputs are PIT-eligible.

    The store enforces immutable parent lineage. This function adds the D2
    contract that every parent is a Tiingo EOD bronze/silver artifact with one
    matching license, and preserves the exact assessment and policy payload.
    """
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise DataQualityError("FACTOR_RISK_EVIDENCE_TIME_INVALID")
    available_at = published_at.astimezone(timezone.utc)
    if assessment.as_of > available_at or not source_manifest_ids or len(set(source_manifest_ids)) != len(source_manifest_ids):
        raise DataQualityError("FACTOR_RISK_EVIDENCE_CONTEXT_INVALID")
    parents = tuple(sorted(source_manifest_ids))
    manifests = []
    for identifier in parents:
        try:
            manifest, _ = store.read(identifier)
        except (FileNotFoundError, ValueError) as exc:
            raise DataQualityError("FACTOR_RISK_EVIDENCE_PARENT_UNVERIFIED") from exc
        parent_available = datetime.fromisoformat(manifest.available_at)
        if (manifest.layer not in {"bronze", "silver"} or manifest.source != "tiingo-eod" or
                parent_available.tzinfo is None or parent_available > available_at):
            raise DataQualityError("FACTOR_RISK_EVIDENCE_PARENT_INVALID")
        manifests.append(manifest)
    licenses = {manifest.license_tag for manifest in manifests}
    if len(licenses) != 1:
        raise DataQualityError("FACTOR_RISK_EVIDENCE_LICENSE_CONFLICT")
    body = {"assessment": assessment.payload(), "specific_risk_policy": specific_risk_policy_payload(policy)}
    request_hash = digest(canonical({"parents": parents, "assessment": body["assessment"],
                                     "specific_risk_policy": body["specific_risk_policy"]}))
    return store.write(
        body, layer="gold", source="tiingo-eod", dataset=FACTOR_RISK_EVIDENCE_DATASET,
        schema_version="factor-specific-risk-evidence@1", retrieved_at=available_at,
        available_at=available_at, provider_timestamp=available_at, license_tag=licenses.pop(),
        code_revision=code_revision, request_hash=request_hash, parent_manifest_ids=parents,
    )
