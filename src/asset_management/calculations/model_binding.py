"""Bind an authorized model scope to an immutable final calculation lineage."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.domain.horizon import SignalValidity
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
    produced_at: datetime
    validity: SignalValidity
    binding_id: str

    def __post_init__(self) -> None:
        if (not self.runtime_run_id.strip() or not self.model_registry_snapshot_id.strip() or
                not self.model_key.strip() or not isinstance(self.scope, ModelScope) or
                any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
                    for value in (self.runtime_binding_hash, self.authorization_hash,
                                  self.lineage_graph_hash, self.final_node_id)) or
                self.bound_at.tzinfo is None or self.bound_at.utcoffset() is None or
                self.produced_at.tzinfo is None or self.produced_at.utcoffset() is None or
                not isinstance(self.validity, SignalValidity)):
            raise InvariantViolation("MODEL_CALCULATION_BINDING_INVALID")
        bound = self.bound_at.astimezone(timezone.utc)
        produced = self.produced_at.astimezone(timezone.utc)
        if produced > bound or self.validity.valid_until <= produced:
            raise InvariantViolation("MODEL_CALCULATION_VALIDITY_TIME_INVALID")
        if self.validity.valid_until <= bound:
            raise InvariantViolation("MODEL_CALCULATION_SIGNAL_EXPIRED")
        body = {"runtime_run_id": self.runtime_run_id,
                "model_registry_snapshot_id": self.model_registry_snapshot_id,
                "runtime_binding_hash": self.runtime_binding_hash,
                "model_key": self.model_key, "scope": self.scope.value,
                "authorization_hash": self.authorization_hash,
                "lineage_graph_hash": self.lineage_graph_hash,
                "final_node_id": self.final_node_id, "bound_at": bound.isoformat(),
                "produced_at": produced.isoformat(), "validity": self.validity.payload()}
        if self.binding_id != digest(canonical(body)):
            raise InvariantViolation("MODEL_CALCULATION_BINDING_HASH_INVALID")
        object.__setattr__(self, "bound_at", bound)
        object.__setattr__(self, "produced_at", produced)

    def payload(self) -> dict[str, object]:
        return {"runtime_run_id": self.runtime_run_id,
                "model_registry_snapshot_id": self.model_registry_snapshot_id,
                "runtime_binding_hash": self.runtime_binding_hash,
                "model_key": self.model_key, "scope": self.scope.value,
                "authorization_hash": self.authorization_hash,
                "lineage_graph_hash": self.lineage_graph_hash,
                "final_node_id": self.final_node_id, "bound_at": self.bound_at.isoformat(),
                "produced_at": self.produced_at.isoformat(), "validity": self.validity.payload(),
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
    validity = SignalValidity.from_payload(output.get("validity"))
    if not isinstance(output.get("as_of"), str):
        raise InvariantViolation("MODEL_CALCULATION_PRODUCTION_TIME_MISSING")
    try:
        produced = datetime.fromisoformat(output["as_of"])
    except ValueError as error:
        raise InvariantViolation("MODEL_CALCULATION_PRODUCTION_TIME_INVALID") from error
    if produced.tzinfo is None or produced.utcoffset() is None:
        raise InvariantViolation("MODEL_CALCULATION_PRODUCTION_TIME_INVALID")
    if "horizon" in output and (type(output["horizon"]) is not int or output["horizon"] != validity.forecast_horizon):
        raise InvariantViolation("MODEL_CALCULATION_HORIZON_MISMATCH")
    bound = bound_at.astimezone(timezone.utc)
    body = {"runtime_run_id": runtime_authorization.runtime_run_id,
            "model_registry_snapshot_id": runtime_authorization.model_registry_snapshot_id,
            "runtime_binding_hash": runtime_authorization.binding_hash,
            "model_key": model_key, "scope": scope.value,
            "authorization_hash": runtime_authorization.authorization.authorization_hash,
            "lineage_graph_hash": lineage.graph_hash, "final_node_id": final.node_id,
            "bound_at": bound.isoformat(), "produced_at": produced.astimezone(timezone.utc).isoformat(),
            "validity": validity.payload()}
    binding = ModelCalculationBinding(
        runtime_authorization.runtime_run_id, runtime_authorization.model_registry_snapshot_id,
        runtime_authorization.binding_hash, model_key, scope,
        runtime_authorization.authorization.authorization_hash, lineage.graph_hash, final.node_id,
        bound, produced, validity, digest(canonical(body)),
    )
    model_registry_evidence.require_authorization(runtime_authorization, model_key=model_key,
                                                 scope=scope, at=produced)
    return binding
