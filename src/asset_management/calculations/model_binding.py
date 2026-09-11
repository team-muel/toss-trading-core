"""Bind an authorized model scope to an immutable final calculation lineage."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import (
    ModelScope, RuntimeModelAuthorization, RuntimeModelRegistryEvidenceRepository,
)

from .lineage import CalculationLineageGraph


@dataclass(frozen=True, slots=True)
class ModelCalculationBinding:
    runtime_run_id: str
    model_registry_snapshot_id: str
    runtime_binding_hash: str
    model_key: str
    scope: ModelScope
    authorization_hash: str
    lineage_graph_hash: str
    final_node_id: str
    bound_at: datetime
    binding_id: str

    def __post_init__(self) -> None:
        if (not self.runtime_run_id.strip() or not self.model_registry_snapshot_id.strip() or
                not self.model_key.strip() or not isinstance(self.scope, ModelScope) or
                any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
                    for value in (self.runtime_binding_hash, self.authorization_hash,
                                  self.lineage_graph_hash, self.final_node_id)) or
                self.bound_at.tzinfo is None or self.bound_at.utcoffset() is None):
            raise InvariantViolation("MODEL_CALCULATION_BINDING_INVALID")
        bound = self.bound_at.astimezone(timezone.utc)
        body = {"runtime_run_id": self.runtime_run_id,
                "model_registry_snapshot_id": self.model_registry_snapshot_id,
                "runtime_binding_hash": self.runtime_binding_hash,
                "model_key": self.model_key, "scope": self.scope.value,
                "authorization_hash": self.authorization_hash,
                "lineage_graph_hash": self.lineage_graph_hash,
                "final_node_id": self.final_node_id, "bound_at": bound.isoformat()}
        if self.binding_id != digest(canonical(body)):
            raise InvariantViolation("MODEL_CALCULATION_BINDING_HASH_INVALID")
        object.__setattr__(self, "bound_at", bound)

    def payload(self) -> dict[str, str]:
        return {"runtime_run_id": self.runtime_run_id,
                "model_registry_snapshot_id": self.model_registry_snapshot_id,
                "runtime_binding_hash": self.runtime_binding_hash,
                "model_key": self.model_key, "scope": self.scope.value,
                "authorization_hash": self.authorization_hash,
                "lineage_graph_hash": self.lineage_graph_hash,
                "final_node_id": self.final_node_id, "bound_at": self.bound_at.isoformat(),
                "binding_id": self.binding_id}


def bind_authorized_model_calculation(*,
                                      model_registry_evidence: RuntimeModelRegistryEvidenceRepository,
                                      runtime_authorization: RuntimeModelAuthorization, model_key: str,
                                      scope: ModelScope, lineage: CalculationLineageGraph,
                                      bound_at: datetime) -> ModelCalculationBinding:
    """Bind only an authorization revalidated from the selected runtime evidence."""
    model_registry_evidence.require_authorization(runtime_authorization, model_key=model_key,
                                                   scope=scope, at=bound_at)
    final = lineage.nodes[lineage.final_node_id]
    output = final.output_value
    if not isinstance(output, Mapping) or output.get("model_key") != model_key or output.get("model_scope") != scope.value:
        raise InvariantViolation("MODEL_CALCULATION_LINEAGE_SCOPE_MISMATCH")
    bound = bound_at.astimezone(timezone.utc)
    body = {"runtime_run_id": runtime_authorization.runtime_run_id,
            "model_registry_snapshot_id": runtime_authorization.model_registry_snapshot_id,
            "runtime_binding_hash": runtime_authorization.binding_hash,
            "model_key": model_key, "scope": scope.value,
            "authorization_hash": runtime_authorization.authorization.authorization_hash,
            "lineage_graph_hash": lineage.graph_hash, "final_node_id": final.node_id,
            "bound_at": bound.isoformat()}
    return ModelCalculationBinding(
        runtime_authorization.runtime_run_id, runtime_authorization.model_registry_snapshot_id,
        runtime_authorization.binding_hash, model_key, scope,
        runtime_authorization.authorization.authorization_hash, lineage.graph_hash, final.node_id,
        bound, digest(canonical(body)),
    )
