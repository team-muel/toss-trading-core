"""AMA-38 model registry, lifecycle, and approved-scope enforcement."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation


class ModelStatus(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class ModelScope(StrEnum):
    FEATURE_CALCULATION = "FEATURE_CALCULATION"
    STATE_INFERENCE = "STATE_INFERENCE"
    REQUIRED_RETURN = "REQUIRED_RETURN"
    PRICING_BASELINE_RETURN = "PRICING_BASELINE_RETURN"
    MODEL_RELATIVE_ALPHA = "MODEL_RELATIVE_ALPHA"
    EXPECTED_BENCHMARK_ACTIVE_RETURN = "EXPECTED_BENCHMARK_ACTIVE_RETURN"
    REALIZED_ACTIVE_RETURN = "REALIZED_ACTIVE_RETURN"
    REGRESSION_ALPHA = "REGRESSION_ALPHA"
    EXPECTED_RETURN = "EXPECTED_RETURN"
    RISK_ESTIMATION = "RISK_ESTIMATION"
    POSITION_SIZING = "POSITION_SIZING"
    ORDER_CREATION = "ORDER_CREATION"


_TRANSITIONS = {
    ModelStatus.DEVELOPMENT: {ModelStatus.VALIDATED, ModelStatus.RETIRED},
    ModelStatus.VALIDATED: {ModelStatus.APPROVED, ModelStatus.DEVELOPMENT, ModelStatus.RETIRED},
    ModelStatus.APPROVED: {ModelStatus.ACTIVE, ModelStatus.SUSPENDED, ModelStatus.RETIRED},
    ModelStatus.ACTIVE: {ModelStatus.DEGRADED, ModelStatus.SUSPENDED, ModelStatus.RETIRED},
    ModelStatus.DEGRADED: {ModelStatus.ACTIVE, ModelStatus.SUSPENDED, ModelStatus.RETIRED},
    ModelStatus.SUSPENDED: {ModelStatus.VALIDATED, ModelStatus.RETIRED},
    ModelStatus.RETIRED: set(),
}


def _identifiers(values: tuple[str, ...], reason: str) -> tuple[str, ...]:
    if (not values or len(values) != len(set(values)) or
            any(not isinstance(value, str) or not value.strip() for value in values)):
        raise InvariantViolation(reason)
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    model_id: str
    version: str
    purpose: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    approved_scope: tuple[ModelScope, ...]
    known_failure_modes: tuple[str, ...]
    validation_date: date
    review_date: date
    owner: str
    status: ModelStatus = ModelStatus.DEVELOPMENT

    def __post_init__(self) -> None:
        if (any(not isinstance(value, str) or not value.strip()
                for value in (self.model_id, self.version, self.purpose, self.owner)) or
                not isinstance(self.status, ModelStatus) or
                not isinstance(self.validation_date, date) or not isinstance(self.review_date, date) or
                self.review_date < self.validation_date or not self.approved_scope or
                len(self.approved_scope) != len(set(self.approved_scope)) or
                any(not isinstance(scope, ModelScope) for scope in self.approved_scope)):
            raise InvariantViolation("MODEL_DEFINITION_INVALID")
        object.__setattr__(self, "inputs", _identifiers(self.inputs, "MODEL_INPUTS_INVALID"))
        object.__setattr__(self, "outputs", _identifiers(self.outputs, "MODEL_OUTPUTS_INVALID"))
        object.__setattr__(self, "known_failure_modes", _identifiers(
            self.known_failure_modes, "MODEL_FAILURE_MODES_INVALID"))
        object.__setattr__(self, "approved_scope", tuple(sorted(
            set(self.approved_scope), key=lambda item: item.value)))
        for scope, output in (
                (ModelScope.PRICING_BASELINE_RETURN, "pricing_baseline_return"),
                (ModelScope.MODEL_RELATIVE_ALPHA, "model_relative_alpha"),
                (ModelScope.EXPECTED_BENCHMARK_ACTIVE_RETURN, "expected_benchmark_active_return"),
                (ModelScope.REALIZED_ACTIVE_RETURN, "realized_active_return"),
                (ModelScope.REGRESSION_ALPHA, "regression_alpha")):
            if ((scope in self.approved_scope or output in self.outputs) and
                    (set(self.approved_scope) != {scope} or self.outputs != (output,))):
                raise InvariantViolation("PRICING_OUTPUT_AUTHORITY_CONFLICT")

    @property
    def key(self) -> str:
        return f"{self.model_id}@{self.version}"

    def payload(self) -> dict[str, object]:
        return {
            "model_id": self.model_id, "version": self.version, "purpose": self.purpose,
            "inputs": list(self.inputs), "outputs": list(self.outputs),
            "approved_scope": [scope.value for scope in self.approved_scope],
            "known_failure_modes": list(self.known_failure_modes),
            "validation_date": self.validation_date.isoformat(),
            "review_date": self.review_date.isoformat(), "owner": self.owner,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class ModelTransition:
    model_key: str
    from_status: ModelStatus
    to_status: ModelStatus
    effective_at: datetime
    reason: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (not self.model_key.strip() or not self.reason.strip() or
                not isinstance(self.from_status, ModelStatus) or
                not isinstance(self.to_status, ModelStatus) or
                self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None):
            raise InvariantViolation("MODEL_TRANSITION_INVALID")
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(timezone.utc))
        object.__setattr__(self, "evidence_ids", _identifiers(
            self.evidence_ids, "MODEL_TRANSITION_EVIDENCE_INVALID"))

    def payload(self) -> dict[str, object]:
        return {"model_key": self.model_key, "from_status": self.from_status.value,
                "to_status": self.to_status.value, "effective_at": self.effective_at.isoformat(),
                "reason": self.reason, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True, slots=True)
class ModelAuthorization:
    model_key: str
    scope: ModelScope
    registry_hash: str
    authorized_at: str
    authorization_hash: str


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ModelDefinition] = {}
        self._transitions: list[ModelTransition] = []

    @property
    def models(self) -> Mapping[str, ModelDefinition]:
        return MappingProxyType(self._models)

    def register(self, model: ModelDefinition) -> None:
        if model.status is not ModelStatus.DEVELOPMENT:
            raise InvariantViolation("MODEL_REGISTRATION_MUST_START_IN_DEVELOPMENT")
        previous = self._models.get(model.key)
        if previous is not None and previous != model:
            raise InvariantViolation("MODEL_DEFINITION_CONFLICT")
        self._models[model.key] = model

    def transition(self, model_key: str, to_status: ModelStatus, *, effective_at: datetime,
                   reason: str, evidence_ids: tuple[str, ...]) -> ModelDefinition:
        try:
            current = self._models[model_key]
        except KeyError:
            raise InvariantViolation("MODEL_NOT_REGISTERED") from None
        if not isinstance(to_status, ModelStatus) or to_status not in _TRANSITIONS[current.status]:
            raise InvariantViolation("MODEL_LIFECYCLE_TRANSITION_INVALID")
        transition = ModelTransition(model_key, current.status, to_status, effective_at,
                                     reason, evidence_ids)
        if (to_status not in {ModelStatus.DEVELOPMENT, ModelStatus.RETIRED} and
                transition.effective_at.date() < current.validation_date):
            raise InvariantViolation("MODEL_TRANSITION_BEFORE_VALIDATION")
        previous_transitions = [item for item in self._transitions if item.model_key == model_key]
        if previous_transitions and transition.effective_at < previous_transitions[-1].effective_at:
            raise InvariantViolation("MODEL_TRANSITION_TIME_REVERSED")
        updated = replace(current, status=to_status)
        self._models[model_key] = updated
        self._transitions.append(transition)
        return updated

    def authorize(self, model_key: str, scope: ModelScope, *, at: datetime) -> ModelAuthorization:
        if at.tzinfo is None or at.utcoffset() is None:
            raise InvariantViolation("MODEL_AUTHORIZATION_TIME_NOT_AWARE")
        try:
            model = self._models[model_key]
        except KeyError:
            raise InvariantViolation("MODEL_NOT_REGISTERED") from None
        if self.status_at(model_key, at=at) is not ModelStatus.ACTIVE:
            raise InvariantViolation("MODEL_NOT_ACTIVE")
        if not isinstance(scope, ModelScope) or scope not in model.approved_scope:
            raise InvariantViolation("MODEL_SCOPE_NOT_APPROVED")
        authorized_at = at.astimezone(timezone.utc)
        if authorized_at.date() < model.validation_date:
            raise InvariantViolation("MODEL_NOT_YET_VALIDATED")
        if authorized_at.date() > model.review_date:
            raise InvariantViolation("MODEL_REVIEW_OVERDUE")
        body = {"model_key": model_key, "scope": scope.value,
                "registry_hash": self.registry_hash, "authorized_at": authorized_at.isoformat()}
        return ModelAuthorization(model_key, scope, self.registry_hash, authorized_at.isoformat(),
                                  digest(canonical(body)))

    def require_authorization(self, authorization: ModelAuthorization, *, model_key: str,
                              scope: ModelScope, at: datetime) -> None:
        if not isinstance(authorization, ModelAuthorization):
            raise InvariantViolation("MODEL_AUTHORIZATION_MISSING")
        if at.tzinfo is None or at.utcoffset() is None:
            raise InvariantViolation("MODEL_AUTHORIZATION_TIME_NOT_AWARE")
        try:
            authorized_at = datetime.fromisoformat(authorization.authorized_at)
        except (TypeError, ValueError):
            raise InvariantViolation("MODEL_AUTHORIZATION_INVALID") from None
        if (authorized_at.tzinfo is None or authorized_at.utcoffset() is None or
                authorized_at.astimezone(timezone.utc).isoformat() != authorization.authorized_at):
            raise InvariantViolation("MODEL_AUTHORIZATION_INVALID")
        body = {"model_key": authorization.model_key, "scope": authorization.scope.value,
                "registry_hash": authorization.registry_hash,
                "authorized_at": authorization.authorized_at}
        if (authorization.authorization_hash != digest(canonical(body)) or
                authorization.registry_hash != self.registry_hash or
                authorization.model_key != model_key or authorization.scope is not scope or
                authorized_at > at):
            raise InvariantViolation("MODEL_AUTHORIZATION_INVALID")
        self.authorize(model_key, scope, at=at)

    def status_at(self, model_key: str, *, at: datetime) -> ModelStatus:
        """Return the lifecycle status effective at the requested UTC instant."""
        if at.tzinfo is None or at.utcoffset() is None:
            raise InvariantViolation("MODEL_AUTHORIZATION_TIME_NOT_AWARE")
        try:
            self._models[model_key]
        except KeyError:
            raise InvariantViolation("MODEL_NOT_REGISTERED") from None
        effective_at = at.astimezone(timezone.utc)
        status = ModelStatus.DEVELOPMENT
        for transition in self._transitions:
            if transition.model_key != model_key or transition.effective_at > effective_at:
                continue
            if transition.from_status is not status:
                raise InvariantViolation("MODEL_LIFECYCLE_HISTORY_INVALID")
            status = transition.to_status
        return status

    @property
    def registry_hash(self) -> str:
        return digest(canonical(self._body()))

    def _body(self) -> dict[str, object]:
        return {"models": [self._models[key].payload() for key in sorted(self._models)],
                "transitions": [item.payload() for item in self._transitions]}

    def payload(self) -> dict[str, object]:
        return {**self._body(), "registry_hash": self.registry_hash}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "ModelRegistry":
        """Reconstruct one exact, content-addressed registry snapshot.

        This intentionally accepts no abbreviated configuration.  Runtime
        selection must preserve the complete lifecycle and prove the supplied
        hash is the hash of the reconstructed authority, not merely a caller
        label.
        """
        if not isinstance(payload, Mapping) or set(payload) != {"models", "transitions", "registry_hash"}:
            raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_INVALID")
        models = payload["models"]
        transitions = payload["transitions"]
        expected_hash = payload["registry_hash"]
        if (not isinstance(models, list) or not isinstance(transitions, list) or
                not isinstance(expected_hash, str) or len(expected_hash) != 64 or
                any(character not in "0123456789abcdef" for character in expected_hash)):
            raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_INVALID")
        registry = cls()
        try:
            for raw in models:
                if not isinstance(raw, Mapping) or set(raw) != {
                    "model_id", "version", "purpose", "inputs", "outputs", "approved_scope",
                    "known_failure_modes", "validation_date", "review_date", "owner", "status",
                }:
                    raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_INVALID")
                definition = ModelDefinition(
                    str(raw["model_id"]), str(raw["version"]), str(raw["purpose"]),
                    tuple(raw["inputs"]), tuple(raw["outputs"]),
                    tuple(ModelScope(value) for value in raw["approved_scope"]),
                    tuple(raw["known_failure_modes"]), date.fromisoformat(str(raw["validation_date"])),
                    date.fromisoformat(str(raw["review_date"])), str(raw["owner"]),
                )
                registry.register(definition)
            for raw in transitions:
                if not isinstance(raw, Mapping) or set(raw) != {
                    "model_key", "from_status", "to_status", "effective_at", "reason", "evidence_ids",
                }:
                    raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_INVALID")
                effective_at = datetime.fromisoformat(str(raw["effective_at"]))
                registry.transition(
                    str(raw["model_key"]), ModelStatus(str(raw["to_status"])),
                    effective_at=effective_at, reason=str(raw["reason"]),
                    evidence_ids=tuple(raw["evidence_ids"]),
                )
        except (TypeError, ValueError, InvariantViolation) as exc:
            if isinstance(exc, InvariantViolation) and str(exc) != "MODEL_REGISTRY_SNAPSHOT_INVALID":
                raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_INVALID") from exc
            raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_INVALID") from exc
        if registry.registry_hash != expected_hash or registry.payload() != dict(payload):
            raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_INVALID")
        return registry

    def publish(self, store: ImmutableDatasetStore) -> str:
        return store.catalog("model-registry", self.payload())
