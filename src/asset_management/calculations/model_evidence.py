"""Immutable authorization-to-lineage evidence for Gate D2."""

from __future__ import annotations

from datetime import datetime, timezone

from asset_management.data.immutable import ImmutableDatasetStore, StoredDatasetManifest, canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import ModelAuthorization, ModelRegistry

from .lineage import CalculationLineageGraph
from .model_binding import ModelCalculationBinding, bind_authorized_model_calculation


MODEL_LINEAGE_EVIDENCE_DATASET = "model-lineage-evidence"
_LICENSE = "purpose=governance;redistribution=forbidden;retention=project"


def model_authorization_payload(authorization: ModelAuthorization) -> dict[str, str]:
    return {"model_key": authorization.model_key, "scope": authorization.scope.value,
            "registry_hash": authorization.registry_hash, "authorized_at": authorization.authorized_at,
            "authorization_hash": authorization.authorization_hash}


def publish_model_lineage_evidence(*, store: ImmutableDatasetStore, registry: ModelRegistry,
                                   authorization: ModelAuthorization, binding: ModelCalculationBinding,
                                   lineage: CalculationLineageGraph, published_at: datetime,
                                   code_revision: str) -> StoredDatasetManifest:
    """Bind model authorization, calculation graph, and verified raw parents.

    Publication is impossible unless the live registry still authorizes the
    binding at the publication time and each raw node resolves in the immutable
    store. The resulting gold manifest retains every raw input as a parent.
    """
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise InvariantViolation("MODEL_LINEAGE_EVIDENCE_TIME_INVALID")
    available_at = published_at.astimezone(timezone.utc)
    registry.require_authorization(authorization, model_key=binding.model_key, scope=binding.scope, at=available_at)
    if (binding.authorization_hash != authorization.authorization_hash or
            binding.lineage_graph_hash != lineage.graph_hash or
            binding.final_node_id != lineage.final_node_id or binding.bound_at > available_at):
        raise InvariantViolation("MODEL_LINEAGE_EVIDENCE_BINDING_INVALID")
    # A self-consistent hash is not proof that the binding was issued by the
    # canonical scope validator. Re-run it at the claimed binding instant.
    rebuilt = bind_authorized_model_calculation(
        model_registry=registry, authorization=authorization, model_key=binding.model_key,
        scope=binding.scope, lineage=lineage, bound_at=binding.bound_at)
    if rebuilt != binding:
        raise InvariantViolation("MODEL_LINEAGE_EVIDENCE_BINDING_INVALID")
    lineage.verify_raw_manifests(store)
    parents = tuple(sorted(node.raw_manifest_id for node in lineage.trace() if node.raw_manifest_id is not None))
    if not parents:
        raise InvariantViolation("MODEL_LINEAGE_EVIDENCE_RAW_MISSING")
    for identifier in parents:
        manifest, _ = store.read(identifier)
        if datetime.fromisoformat(manifest.available_at) > available_at:
            raise InvariantViolation("MODEL_LINEAGE_EVIDENCE_RAW_AFTER_CUTOFF")
    # `authorization` is a generic secret-redaction key in the immutable store;
    # use a precise, non-secret field name so the signed authorization payload
    # remains auditable rather than being redacted on publication.
    body = {"registry": registry.payload(), "model_authorization": model_authorization_payload(authorization),
            "binding": binding.payload(), "lineage": lineage.payload()}
    return store.write(
        body, layer="gold", source="model-lineage", dataset=MODEL_LINEAGE_EVIDENCE_DATASET,
        schema_version="model-lineage-evidence@1", retrieved_at=available_at, available_at=available_at,
        provider_timestamp=available_at, license_tag=_LICENSE, code_revision=code_revision,
        request_hash=digest(canonical({"parents": parents, **body})), parent_manifest_ids=parents,
        allow_mixed_parent_contracts=True,
    )
