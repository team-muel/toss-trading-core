"""Runtime-independent pre-execution decision kernel and parity evidence."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.decisions.governor import DecisionState
from asset_management.domain.errors import InvariantViolation


_HASH = re.compile(r"[0-9a-f]{64}")
_PERSISTED_PIPELINE_ASSEMBLER = object()


def _text(value: str, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvariantViolation(reason)
    return value


def _utc(value: datetime, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


def _ids(values: tuple[str, ...], reason: str, *, hashes: bool = False) -> tuple[str, ...]:
    if not values or len(values) != len(set(values)):
        raise InvariantViolation(reason)
    result = tuple(sorted(_text(value, reason) for value in values))
    if hashes and any(_HASH.fullmatch(value) is None for value in result):
        raise InvariantViolation(reason)
    return result


def _decimal_map(values: Mapping[str, Decimal], reason: str, *, weights: bool = False) -> Mapping[str, Decimal]:
    if not isinstance(values, Mapping) or not values:
        raise InvariantViolation(reason)
    result: dict[str, Decimal] = {}
    for key, value in values.items():
        if not isinstance(value, Decimal) or not value.is_finite():
            raise InvariantViolation(reason)
        result[_text(key, reason)] = value
    if weights and (any(value < 0 for value in result.values()) or sum(result.values()) != Decimal(1)):
        raise InvariantViolation(reason)
    return MappingProxyType(dict(sorted(result.items())))


def _empty_decimal_map(values: Mapping[str, Decimal], reason: str) -> Mapping[str, Decimal]:
    if not isinstance(values, Mapping) or values:
        raise InvariantViolation(reason)
    return MappingProxyType({})


def _text_map(values: Mapping[str, str], reason: str, *, required: bool) -> Mapping[str, str]:
    if not isinstance(values, Mapping) or (required and not values):
        raise InvariantViolation(reason)
    return MappingProxyType(dict(sorted((_text(key, reason), _text(value, reason)) for key, value in values.items())))


class DecisionRuntime(StrEnum):
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


@dataclass(frozen=True, slots=True)
class PricingApplicabilityEvidence:
    """Content-addressed policy evidence deciding whether pricing is required."""

    scope_key: str
    applicable: bool
    reason: str | None
    policy_version: str
    evidence_id: str

    def __post_init__(self) -> None:
        _text(self.scope_key, "DECISION_KERNEL_PRICING_AUTHORITY_INVALID")
        _text(self.policy_version, "DECISION_KERNEL_PRICING_AUTHORITY_INVALID")
        if not isinstance(self.applicable, bool):
            raise InvariantViolation("DECISION_KERNEL_PRICING_AUTHORITY_INVALID")
        if self.applicable:
            if self.reason is not None:
                raise InvariantViolation("DECISION_KERNEL_PRICING_AUTHORITY_INVALID")
        else:
            _text(self.reason, "DECISION_KERNEL_PRICING_AUTHORITY_INVALID")
        if not isinstance(self.evidence_id, str) or _HASH.fullmatch(self.evidence_id) is None:
            raise InvariantViolation("DECISION_KERNEL_PRICING_AUTHORITY_INVALID")
        if self.evidence_id != digest(canonical(self.authority_payload())):
            raise InvariantViolation("DECISION_KERNEL_PRICING_AUTHORITY_HASH_INVALID")

    def authority_payload(self) -> dict[str, object]:
        return {
            "scope_key": self.scope_key,
            "applicable": self.applicable,
            "reason": self.reason,
            "policy_version": self.policy_version,
        }

    def payload(self) -> dict[str, object]:
        return self.authority_payload() | {"evidence_id": self.evidence_id}

    @classmethod
    def create(cls, *, scope_key: str, applicable: bool, reason: str | None,
               policy_version: str) -> "PricingApplicabilityEvidence":
        body = {
            "scope_key": scope_key,
            "applicable": applicable,
            "reason": reason,
            "policy_version": policy_version,
        }
        return cls(scope_key, applicable, reason, policy_version, digest(canonical(body)))


@dataclass(frozen=True, slots=True)
class FrozenDecisionInput:
    snapshot_id: str
    strategy_key: str
    model_keys: tuple[str, ...]
    policy_versions: Mapping[str, str]
    parameter_set_key: str
    input_manifest_ids: tuple[str, ...]
    as_of: datetime
    information_cutoff: datetime
    code_revision: str
    pricing_applicability_evidence: PricingApplicabilityEvidence

    def __post_init__(self) -> None:
        for value in (self.snapshot_id, self.strategy_key, self.parameter_set_key, self.code_revision):
            _text(value, "DECISION_KERNEL_INPUT_INVALID")
        as_of = _utc(self.as_of, "DECISION_KERNEL_TIME_INVALID")
        cutoff = _utc(self.information_cutoff, "DECISION_KERNEL_TIME_INVALID")
        if cutoff > as_of:
            raise InvariantViolation("DECISION_KERNEL_CUTOFF_AFTER_AS_OF")
        object.__setattr__(self, "model_keys", _ids(self.model_keys, "DECISION_KERNEL_MODELS_INVALID"))
        policies = _text_map(self.policy_versions, "DECISION_KERNEL_POLICIES_INVALID", required=True)
        object.__setattr__(self, "policy_versions", policies)
        manifests = _ids(self.input_manifest_ids, "DECISION_KERNEL_MANIFESTS_INVALID", hashes=True)
        object.__setattr__(self, "input_manifest_ids", manifests)
        evidence = self.pricing_applicability_evidence
        if not isinstance(evidence, PricingApplicabilityEvidence):
            raise InvariantViolation("DECISION_KERNEL_PRICING_AUTHORITY_INVALID")
        if policies.get("pricing_applicability") != evidence.policy_version:
            raise InvariantViolation("DECISION_KERNEL_PRICING_AUTHORITY_POLICY_MISMATCH")
        if evidence.evidence_id not in manifests:
            raise InvariantViolation("DECISION_KERNEL_PRICING_AUTHORITY_NOT_IN_LINEAGE")
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "information_cutoff", cutoff)

    def payload(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id, "strategy_key": self.strategy_key,
            "model_keys": list(self.model_keys), "policy_versions": dict(self.policy_versions),
            "parameter_set_key": self.parameter_set_key,
            "input_manifest_ids": list(self.input_manifest_ids), "as_of": self.as_of.isoformat(),
            "information_cutoff": self.information_cutoff.isoformat(), "code_revision": self.code_revision,
            "pricing_applicability_evidence": self.pricing_applicability_evidence.payload(),
        }

    @property
    def input_hash(self) -> str:
        return digest(canonical(self.payload()))


@dataclass(frozen=True, slots=True)
class CanonicalDecisionRequest:
    """Typed, runtime-independent assembly for one pre-execution decision.

    Callers provide immutable input and the authoritative outputs produced by
    the canonical pipeline.  They cannot inject a calculation callback or
    select a different calculation for a runtime adapter.
    """

    inputs: FrozenDecisionInput
    feature_values: Mapping[str, Decimal]
    signal_values: Mapping[str, Decimal]
    forecast_values: Mapping[str, Decimal]
    pricing_outputs: Mapping[str, Decimal]
    risk_outputs: Mapping[str, Decimal]
    target_weights: Mapping[str, Decimal]
    risk_decision_id: str
    risk_decision_hash: str
    risk_state: DecisionState
    risk_reason_codes: tuple[str, ...]
    order_intent_economics: Mapping[str, str]
    data_lineage_ids: tuple[str, ...]
    calculation_lineage_ids: tuple[str, ...]
    pricing_applicability_evidence_id: str
    pricing_applicable: bool = True
    pricing_non_applicability_reason: str | None = None
    _assembler: object | None = field(default=None, repr=False, compare=False)
    _assembly_hash: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def _from_persisted_pipeline(cls, **values: object) -> "CanonicalDecisionRequest":
        """Capability used only by the persistent pipeline assembler."""
        request = cls(**values, _assembler=_PERSISTED_PIPELINE_ASSEMBLER)
        decision = request.build_decision()
        return replace(request, _assembly_hash=digest(canonical({
            "input_hash": request.inputs.input_hash,
            "decision": decision.payload(),
        })))

    def require_persisted_assembly(self) -> None:
        if self._assembler is not _PERSISTED_PIPELINE_ASSEMBLER or self._assembly_hash is None:
            raise InvariantViolation("CANONICAL_DECISION_ASSEMBLY_REQUIRED")
        decision = self.build_decision()
        expected = digest(canonical({"input_hash": self.inputs.input_hash, "decision": decision.payload()}))
        if self._assembly_hash != expected:
            raise InvariantViolation("CANONICAL_DECISION_ASSEMBLY_TAMPERED")

    def build_decision(self) -> "PreExecutionDecision":
        if not isinstance(self.inputs, FrozenDecisionInput):
            raise InvariantViolation("CANONICAL_DECISION_INPUT_INVALID")
        return PreExecutionDecision(
            feature_values=self.feature_values,
            signal_values=self.signal_values,
            forecast_values=self.forecast_values,
            pricing_outputs=self.pricing_outputs,
            risk_outputs=self.risk_outputs,
            target_weights=self.target_weights,
            risk_decision_id=self.risk_decision_id,
            risk_decision_hash=self.risk_decision_hash,
            risk_state=self.risk_state,
            risk_reason_codes=self.risk_reason_codes,
            order_intent_economics=self.order_intent_economics,
            data_lineage_ids=self.data_lineage_ids,
            calculation_lineage_ids=self.calculation_lineage_ids,
            pricing_applicability_evidence_id=self.pricing_applicability_evidence_id,
            pricing_applicable=self.pricing_applicable,
            pricing_non_applicability_reason=self.pricing_non_applicability_reason,
        )


@dataclass(frozen=True, slots=True)
class PreExecutionDecision:
    feature_values: Mapping[str, Decimal]
    signal_values: Mapping[str, Decimal]
    forecast_values: Mapping[str, Decimal]
    pricing_outputs: Mapping[str, Decimal]
    risk_outputs: Mapping[str, Decimal]
    target_weights: Mapping[str, Decimal]
    risk_decision_id: str
    risk_decision_hash: str
    risk_state: DecisionState
    risk_reason_codes: tuple[str, ...]
    order_intent_economics: Mapping[str, str]
    data_lineage_ids: tuple[str, ...]
    calculation_lineage_ids: tuple[str, ...]
    pricing_applicability_evidence_id: str
    pricing_applicable: bool = True
    pricing_non_applicability_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("feature_values", "signal_values", "forecast_values", "risk_outputs"):
            object.__setattr__(self, name, _decimal_map(getattr(self, name), "DECISION_KERNEL_OUTPUT_INVALID"))
        if (not isinstance(self.pricing_applicability_evidence_id, str) or
                _HASH.fullmatch(self.pricing_applicability_evidence_id) is None):
            raise InvariantViolation("DECISION_KERNEL_PRICING_AUTHORITY_INVALID")
        if not isinstance(self.pricing_applicable, bool):
            raise InvariantViolation("DECISION_KERNEL_PRICING_APPLICABILITY_INVALID")
        if self.pricing_applicable:
            object.__setattr__(self, "pricing_outputs", _decimal_map(
                self.pricing_outputs, "DECISION_KERNEL_OUTPUT_INVALID"))
            if self.pricing_non_applicability_reason is not None:
                raise InvariantViolation("DECISION_KERNEL_PRICING_APPLICABILITY_INVALID")
        else:
            object.__setattr__(self, "pricing_outputs", _empty_decimal_map(
                self.pricing_outputs, "DECISION_KERNEL_PRICING_APPLICABILITY_INVALID"))
            object.__setattr__(self, "pricing_non_applicability_reason", _text(
                self.pricing_non_applicability_reason,
                "DECISION_KERNEL_PRICING_APPLICABILITY_INVALID",
            ))
        object.__setattr__(self, "target_weights", _decimal_map(
            self.target_weights, "DECISION_KERNEL_TARGET_INVALID", weights=True))
        _text(self.risk_decision_id, "DECISION_KERNEL_RISK_DECISION_INVALID")
        if not isinstance(self.risk_decision_hash, str) or _HASH.fullmatch(self.risk_decision_hash) is None:
            raise InvariantViolation("DECISION_KERNEL_RISK_DECISION_INVALID")
        if not isinstance(self.risk_state, DecisionState):
            raise InvariantViolation("DECISION_KERNEL_RISK_DECISION_INVALID")
        if self.risk_state in {DecisionState.ALLOW, DecisionState.REDUCE} and not self.pricing_applicable:
            raise InvariantViolation("DECISION_KERNEL_PRICING_REQUIRED_FOR_AUTHORIZED_TARGET")
        object.__setattr__(self, "risk_reason_codes", _ids(
            self.risk_reason_codes, "DECISION_KERNEL_RISK_REASONS_INVALID") if self.risk_reason_codes else ())
        economics = _text_map(self.order_intent_economics, "DECISION_KERNEL_ORDER_ECONOMICS_INVALID",
                              required=self.risk_state in {DecisionState.ALLOW, DecisionState.REDUCE})
        if self.risk_state not in {DecisionState.ALLOW, DecisionState.REDUCE} and economics:
            raise InvariantViolation("DECISION_KERNEL_ORDER_ECONOMICS_INVALID")
        object.__setattr__(self, "order_intent_economics", economics)
        object.__setattr__(self, "data_lineage_ids", _ids(
            self.data_lineage_ids, "DECISION_KERNEL_DATA_LINEAGE_INVALID", hashes=True))
        object.__setattr__(self, "calculation_lineage_ids", _ids(
            self.calculation_lineage_ids, "DECISION_KERNEL_CALCULATION_LINEAGE_INVALID", hashes=True))

    def payload(self) -> dict[str, object]:
        values = lambda source: {key: str(value) for key, value in source.items()}
        return {
            "feature_values": values(self.feature_values), "signal_values": values(self.signal_values),
            "forecast_values": values(self.forecast_values), "pricing_outputs": values(self.pricing_outputs),
            "pricing_applicability_evidence_id": self.pricing_applicability_evidence_id,
            "pricing_applicable": self.pricing_applicable,
            "pricing_non_applicability_reason": self.pricing_non_applicability_reason,
            "risk_outputs": values(self.risk_outputs), "target_weights": values(self.target_weights),
            "risk_decision_id": self.risk_decision_id, "risk_decision_hash": self.risk_decision_hash,
            "risk_state": self.risk_state.value, "risk_reason_codes": list(self.risk_reason_codes),
            "order_intent_economics": dict(self.order_intent_economics),
            "data_lineage_ids": list(self.data_lineage_ids),
            "calculation_lineage_ids": list(self.calculation_lineage_ids),
        }


@dataclass(frozen=True, slots=True)
class RuntimeAdapterDescriptor:
    runtime: DecisionRuntime
    clock_adapter_key: str
    data_source_adapter_key: str
    broker_adapter_key: str
    execution_adapter_key: str
    persistence_adapter_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, DecisionRuntime):
            raise InvariantViolation("DECISION_RUNTIME_ADAPTER_INVALID")
        for value in (self.clock_adapter_key, self.data_source_adapter_key, self.broker_adapter_key,
                      self.execution_adapter_key, self.persistence_adapter_key):
            _text(value, "DECISION_RUNTIME_ADAPTER_INVALID")

    def payload(self) -> dict[str, str]:
        return {"runtime": self.runtime.value, "clock_adapter_key": self.clock_adapter_key,
                "data_source_adapter_key": self.data_source_adapter_key,
                "broker_adapter_key": self.broker_adapter_key,
                "execution_adapter_key": self.execution_adapter_key,
                "persistence_adapter_key": self.persistence_adapter_key}


@dataclass(frozen=True, slots=True)
class DecisionKernelEvaluation:
    kernel_version: str
    input_hash: str
    adapter: RuntimeAdapterDescriptor
    decision: PreExecutionDecision
    semantic_hash: str

    def __post_init__(self) -> None:
        _text(self.kernel_version, "DECISION_KERNEL_EVALUATION_INVALID")
        if (not isinstance(self.input_hash, str) or _HASH.fullmatch(self.input_hash) is None or
                not isinstance(self.adapter, RuntimeAdapterDescriptor) or
                not isinstance(self.decision, PreExecutionDecision) or
                not isinstance(self.semantic_hash, str) or _HASH.fullmatch(self.semantic_hash) is None):
            raise InvariantViolation("DECISION_KERNEL_EVALUATION_INVALID")
        expected = digest(canonical({"kernel_version": self.kernel_version, "input_hash": self.input_hash,
                                     "decision": self.decision.payload()}))
        if self.semantic_hash != expected:
            raise InvariantViolation("DECISION_KERNEL_SEMANTIC_HASH_INVALID")

    def payload(self) -> dict[str, object]:
        return {"kernel_version": self.kernel_version, "input_hash": self.input_hash,
                "adapter": self.adapter.payload(), "decision": self.decision.payload(),
                "semantic_hash": self.semantic_hash}

    @property
    def evaluation_hash(self) -> str:
        return digest(canonical(self.payload()))


class DecisionKernel:
    """Evaluates the one typed canonical decision assembly for every adapter."""

    def __init__(self, kernel_version: str) -> None:
        self.kernel_version = _text(kernel_version, "DECISION_KERNEL_VERSION_INVALID")

    def evaluate(self, request: CanonicalDecisionRequest,
                 adapter: RuntimeAdapterDescriptor) -> DecisionKernelEvaluation:
        if not isinstance(request, CanonicalDecisionRequest) or not isinstance(adapter, RuntimeAdapterDescriptor):
            raise InvariantViolation("DECISION_KERNEL_EVALUATION_INVALID")
        request.require_persisted_assembly()
        inputs = request.inputs
        decision = request.build_decision()
        evidence = inputs.pricing_applicability_evidence
        if (decision.pricing_applicability_evidence_id != evidence.evidence_id or
                decision.pricing_applicable != evidence.applicable or
                decision.pricing_non_applicability_reason != evidence.reason):
            raise InvariantViolation("DECISION_KERNEL_PRICING_AUTHORITY_MISMATCH")
        semantic_hash = digest(canonical({"kernel_version": self.kernel_version,
                                          "input_hash": inputs.input_hash,
                                          "decision": decision.payload()}))
        return DecisionKernelEvaluation(self.kernel_version, inputs.input_hash, adapter, decision, semantic_hash)


class DecisionRuntimeAdapter:
    """Public runtime entry point backed exclusively by persisted pipeline evidence."""

    def __init__(self, kernel: DecisionKernel, descriptor: RuntimeAdapterDescriptor, *,
                 repository: object, runtime_run_id: str,
                 pricing_applicability_evidence: PricingApplicabilityEvidence) -> None:
        from .pipelines import PipelineEvidenceRepository

        if (not isinstance(kernel, DecisionKernel) or not isinstance(descriptor, RuntimeAdapterDescriptor) or
                not isinstance(repository, PipelineEvidenceRepository) or
                not isinstance(pricing_applicability_evidence, PricingApplicabilityEvidence)):
            raise InvariantViolation("DECISION_RUNTIME_ADAPTER_INVALID")
        _text(runtime_run_id, "DECISION_RUNTIME_ADAPTER_INVALID")
        self._kernel = kernel
        self.descriptor = descriptor
        self._repository = repository
        self._runtime_run_id = runtime_run_id
        self._pricing_applicability_evidence = pricing_applicability_evidence

    def decide(self) -> DecisionKernelEvaluation:
        """Assemble and evaluate; callers cannot supply economic values or a request."""

        request = self._repository.assemble_canonical_decision_request(
            self._runtime_run_id,
            pricing_applicability_evidence=self._pricing_applicability_evidence,
        )
        return self._kernel.evaluate(request, self.descriptor)


class DecisionParityLedger:
    """Append-only parity evidence; a runtime cannot record a divergent semantic decision."""

    def __init__(self) -> None:
        self._evaluations: dict[tuple[str, DecisionRuntime], DecisionKernelEvaluation] = {}

    def record(self, evaluation: DecisionKernelEvaluation) -> DecisionKernelEvaluation:
        if not isinstance(evaluation, DecisionKernelEvaluation):
            raise InvariantViolation("DECISION_KERNEL_EVALUATION_INVALID")
        key = (evaluation.input_hash, evaluation.adapter.runtime)
        previous = self._evaluations.get(key)
        if previous is not None and previous != evaluation:
            raise InvariantViolation("DECISION_RUNTIME_EVIDENCE_CONFLICT")
        related = [item for (input_hash, _), item in self._evaluations.items()
                   if input_hash == evaluation.input_hash]
        if related and any(item.semantic_hash != evaluation.semantic_hash for item in related):
            raise InvariantViolation("DECISION_KERNEL_PARITY_MISMATCH")
        self._evaluations[key] = evaluation
        return evaluation

    def require_parity(self, input_hash: str, *, runtimes: tuple[DecisionRuntime, ...] = (
            DecisionRuntime.HISTORICAL_REPLAY, DecisionRuntime.PAPER,
            DecisionRuntime.SHADOW, DecisionRuntime.LIVE)) -> str:
        if not isinstance(input_hash, str) or _HASH.fullmatch(input_hash) is None:
            raise InvariantViolation("DECISION_KERNEL_INPUT_HASH_INVALID")
        if not runtimes or len(runtimes) != len(set(runtimes)) or any(not isinstance(item, DecisionRuntime) for item in runtimes):
            raise InvariantViolation("DECISION_RUNTIME_SET_INVALID")
        found = {runtime for (item_hash, runtime) in self._evaluations if item_hash == input_hash}
        if not set(runtimes) <= found:
            raise InvariantViolation("DECISION_PARITY_EVIDENCE_INCOMPLETE")
        hashes = {self._evaluations[(input_hash, runtime)].semantic_hash for runtime in runtimes}
        if len(hashes) != 1:
            raise InvariantViolation("DECISION_KERNEL_PARITY_MISMATCH")
        return hashes.pop()

    @property
    def ledger_hash(self) -> str:
        return digest(canonical(self._body()))

    def _body(self) -> dict[str, object]:
        evaluations = sorted(self._evaluations.values(), key=lambda item: (item.input_hash, item.adapter.runtime.value))
        return {"evaluations": [item.payload() | {"evaluation_hash": item.evaluation_hash} for item in evaluations]}

    def payload(self) -> dict[str, object]:
        return self._body() | {"ledger_hash": self.ledger_hash}

    def publish(self, store: ImmutableDatasetStore) -> str:
        return store.catalog("decision-parity-ledger", self.payload())
