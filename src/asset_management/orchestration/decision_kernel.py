"""Runtime-independent pre-execution decision kernel and parity evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Callable, Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.decisions.governor import DecisionState
from asset_management.domain.errors import InvariantViolation


_HASH = re.compile(r"[0-9a-f]{64}")


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

    def __post_init__(self) -> None:
        for value in (self.snapshot_id, self.strategy_key, self.parameter_set_key, self.code_revision):
            _text(value, "DECISION_KERNEL_INPUT_INVALID")
        as_of = _utc(self.as_of, "DECISION_KERNEL_TIME_INVALID")
        cutoff = _utc(self.information_cutoff, "DECISION_KERNEL_TIME_INVALID")
        if cutoff > as_of:
            raise InvariantViolation("DECISION_KERNEL_CUTOFF_AFTER_AS_OF")
        object.__setattr__(self, "model_keys", _ids(self.model_keys, "DECISION_KERNEL_MODELS_INVALID"))
        object.__setattr__(self, "policy_versions", _text_map(
            self.policy_versions, "DECISION_KERNEL_POLICIES_INVALID", required=True))
        object.__setattr__(self, "input_manifest_ids", _ids(
            self.input_manifest_ids, "DECISION_KERNEL_MANIFESTS_INVALID", hashes=True))
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "information_cutoff", cutoff)

    def payload(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id, "strategy_key": self.strategy_key,
            "model_keys": list(self.model_keys), "policy_versions": dict(self.policy_versions),
            "parameter_set_key": self.parameter_set_key,
            "input_manifest_ids": list(self.input_manifest_ids), "as_of": self.as_of.isoformat(),
            "information_cutoff": self.information_cutoff.isoformat(), "code_revision": self.code_revision,
        }

    @property
    def input_hash(self) -> str:
        return digest(canonical(self.payload()))


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

    def __post_init__(self) -> None:
        for name in ("feature_values", "signal_values", "forecast_values", "pricing_outputs", "risk_outputs"):
            object.__setattr__(self, name, _decimal_map(getattr(self, name), "DECISION_KERNEL_OUTPUT_INVALID"))
        object.__setattr__(self, "target_weights", _decimal_map(
            self.target_weights, "DECISION_KERNEL_TARGET_INVALID", weights=True))
        _text(self.risk_decision_id, "DECISION_KERNEL_RISK_DECISION_INVALID")
        if not isinstance(self.risk_decision_hash, str) or _HASH.fullmatch(self.risk_decision_hash) is None:
            raise InvariantViolation("DECISION_KERNEL_RISK_DECISION_INVALID")
        if not isinstance(self.risk_state, DecisionState):
            raise InvariantViolation("DECISION_KERNEL_RISK_DECISION_INVALID")
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
    """Runs the same pre-execution calculation callable for every runtime adapter."""

    def __init__(self, kernel_version: str,
                 calculate: Callable[[FrozenDecisionInput], PreExecutionDecision]) -> None:
        self.kernel_version = _text(kernel_version, "DECISION_KERNEL_VERSION_INVALID")
        if not callable(calculate):
            raise InvariantViolation("DECISION_KERNEL_CALCULATOR_INVALID")
        self._calculate = calculate

    def evaluate(self, inputs: FrozenDecisionInput, adapter: RuntimeAdapterDescriptor) -> DecisionKernelEvaluation:
        if not isinstance(inputs, FrozenDecisionInput) or not isinstance(adapter, RuntimeAdapterDescriptor):
            raise InvariantViolation("DECISION_KERNEL_EVALUATION_INVALID")
        decision = self._calculate(inputs)
        if not isinstance(decision, PreExecutionDecision):
            raise InvariantViolation("DECISION_KERNEL_CALCULATOR_INVALID")
        semantic_hash = digest(canonical({"kernel_version": self.kernel_version,
                                          "input_hash": inputs.input_hash,
                                          "decision": decision.payload()}))
        return DecisionKernelEvaluation(self.kernel_version, inputs.input_hash, adapter, decision, semantic_hash)


class DecisionRuntimeAdapter:
    """Adapter boundary: it identifies runtime I/O but cannot alter kernel inputs or outputs."""

    def __init__(self, kernel: DecisionKernel, descriptor: RuntimeAdapterDescriptor) -> None:
        if not isinstance(kernel, DecisionKernel) or not isinstance(descriptor, RuntimeAdapterDescriptor):
            raise InvariantViolation("DECISION_RUNTIME_ADAPTER_INVALID")
        self._kernel = kernel
        self.descriptor = descriptor

    def decide(self, inputs: FrozenDecisionInput) -> DecisionKernelEvaluation:
        return self._kernel.evaluate(inputs, self.descriptor)


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
