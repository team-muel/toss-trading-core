"""Immutable preregistration and outcome evidence for validation backtest runs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import InvestorMandateRegistry, MandateObjective


_HASH = re.compile(r"[0-9a-f]{64}")


def _text(value: str, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvariantViolation(reason)
    return value


def _utc(value: datetime, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


def _decimal(value: Decimal, reason: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantViolation(reason)
    return value


def _ids(values: tuple[str, ...], reason: str) -> tuple[str, ...]:
    if not values or len(values) != len(set(values)):
        raise InvariantViolation(reason)
    return tuple(sorted(_text(value, reason) for value in values))


def _manifests(values: tuple[str, ...]) -> tuple[str, ...]:
    values = _ids(values, "BACKTEST_DATASET_MANIFESTS_INVALID")
    if any(_HASH.fullmatch(value) is None for value in values):
        raise InvariantViolation("BACKTEST_DATASET_MANIFESTS_INVALID")
    return values


def _thresholds(values: Mapping[str, Decimal]) -> Mapping[str, Decimal]:
    if not isinstance(values, Mapping) or not values:
        raise InvariantViolation("BACKTEST_ACCEPTANCE_THRESHOLDS_INVALID")
    result: dict[str, Decimal] = {}
    for key, value in values.items():
        result[_text(key, "BACKTEST_ACCEPTANCE_THRESHOLDS_INVALID")] = _decimal(
            value, "BACKTEST_ACCEPTANCE_THRESHOLDS_INVALID")
    return MappingProxyType(dict(sorted(result.items())))


@dataclass(frozen=True, slots=True)
class BacktestPeriod:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = _utc(self.start, "BACKTEST_PERIOD_INVALID")
        end = _utc(self.end, "BACKTEST_PERIOD_INVALID")
        if end <= start:
            raise InvariantViolation("BACKTEST_PERIOD_INVALID")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def payload(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True, slots=True)
class BacktestRunSpec:
    hypothesis_id: str
    experiment_id: str
    run_version: str
    strategy_key: str
    mandate_key: str
    objective: MandateObjective
    reporting_currency: str
    universe_version: str
    pit_universe_rule_key: str
    universe_manifest_id: str
    dataset_manifest_ids: tuple[str, ...]
    information_cutoff: datetime
    train_period: BacktestPeriod
    validation_period: BacktestPeriod
    test_period: BacktestPeriod
    forecast_horizon: int
    holding_horizon: int
    rebalance_horizon: int
    benchmark_key: str
    benchmark_total_return: bool
    benchmark_currency: str
    benchmark_rebalance_horizon: int
    primary_oos_objective: str
    strategic_risk_budget: Decimal
    active_risk_budget: Decimal
    tracking_error_budget: Decimal
    max_stress_loss: Decimal
    risk_aversion_policy_key: str
    risk_aversion_min: Decimal
    risk_aversion_max: Decimal
    active_risk_aversion_min: Decimal
    active_risk_aversion_max: Decimal
    hard_constraint_policy_key: str
    acceptance_thresholds: Mapping[str, Decimal]
    transaction_cost_model_key: str
    tax_model_key: str
    fx_model_key: str
    execution_fidelity_model_key: str
    parameter_search_space_key: str
    parameter_search_budget: int
    purge_observations: int
    embargo_observations: int
    robustness_test_keys: tuple[str, ...]
    seed_policy_key: str
    code_revision: str
    parent_spec_hash: str | None = None

    def __post_init__(self) -> None:
        text_fields = (
            self.hypothesis_id, self.experiment_id, self.run_version, self.strategy_key,
            self.mandate_key, self.reporting_currency, self.universe_version,
            self.pit_universe_rule_key, self.universe_manifest_id, self.benchmark_key,
            self.benchmark_currency, self.primary_oos_objective, self.risk_aversion_policy_key,
            self.hard_constraint_policy_key, self.transaction_cost_model_key, self.tax_model_key,
            self.fx_model_key, self.execution_fidelity_model_key, self.parameter_search_space_key,
            self.seed_policy_key, self.code_revision,
        )
        if any(not isinstance(value, str) or not value.strip() for value in text_fields):
            raise InvariantViolation("BACKTEST_RUN_SPEC_INVALID")
        if (not isinstance(self.objective, MandateObjective) or
                type(self.benchmark_total_return) is not bool or
                not _HASH.fullmatch(self.universe_manifest_id)):
            raise InvariantViolation("BACKTEST_RUN_SPEC_INVALID")
        manifests = _manifests(self.dataset_manifest_ids)
        if self.universe_manifest_id not in manifests:
            raise InvariantViolation("BACKTEST_UNIVERSE_MANIFEST_MISSING")
        cutoff = _utc(self.information_cutoff, "BACKTEST_INFORMATION_CUTOFF_INVALID")
        if (not all(isinstance(value, BacktestPeriod) for value in
                    (self.train_period, self.validation_period, self.test_period)) or
                self.train_period.end > self.validation_period.start or
                self.validation_period.end > self.test_period.start):
            raise InvariantViolation("BACKTEST_PERIOD_ORDER_INVALID")
        if (any(type(value) is not int or value <= 0 for value in
                (self.forecast_horizon, self.holding_horizon, self.rebalance_horizon,
                 self.benchmark_rebalance_horizon)) or
                type(self.parameter_search_budget) is not int or self.parameter_search_budget < 0 or
                type(self.purge_observations) is not int or self.purge_observations < 0 or
                type(self.embargo_observations) is not int or self.embargo_observations < 0):
            raise InvariantViolation("BACKTEST_HORIZON_OR_SEARCH_INVALID")
        risk_values = (self.strategic_risk_budget, self.active_risk_budget,
                       self.tracking_error_budget, self.max_stress_loss)
        if any(not Decimal(0) <= _decimal(value, "BACKTEST_RISK_POLICY_INVALID") <= Decimal(1)
               for value in risk_values):
            raise InvariantViolation("BACKTEST_RISK_POLICY_INVALID")
        limits = (self.risk_aversion_min, self.risk_aversion_max,
                  self.active_risk_aversion_min, self.active_risk_aversion_max)
        if (any(_decimal(value, "BACKTEST_RISK_POLICY_INVALID") < 0 for value in limits) or
                self.risk_aversion_min > self.risk_aversion_max or
                self.active_risk_aversion_min > self.active_risk_aversion_max):
            raise InvariantViolation("BACKTEST_RISK_POLICY_INVALID")
        parent = self.parent_spec_hash
        if parent is not None and (not isinstance(parent, str) or _HASH.fullmatch(parent) is None):
            raise InvariantViolation("BACKTEST_PARENT_SPEC_INVALID")
        object.__setattr__(self, "dataset_manifest_ids", manifests)
        object.__setattr__(self, "information_cutoff", cutoff)
        object.__setattr__(self, "acceptance_thresholds", _thresholds(self.acceptance_thresholds))
        object.__setattr__(self, "robustness_test_keys", _ids(self.robustness_test_keys, "BACKTEST_ROBUSTNESS_TESTS_INVALID"))

    @property
    def key(self) -> str:
        return f"{self.hypothesis_id}/{self.experiment_id}@{self.run_version}"

    def payload(self) -> dict[str, object]:
        return {
            "hypothesis_id": self.hypothesis_id, "experiment_id": self.experiment_id,
            "run_version": self.run_version, "strategy_key": self.strategy_key,
            "mandate_key": self.mandate_key, "objective": self.objective.value,
            "reporting_currency": self.reporting_currency, "universe_version": self.universe_version,
            "pit_universe_rule_key": self.pit_universe_rule_key,
            "universe_manifest_id": self.universe_manifest_id,
            "dataset_manifest_ids": list(self.dataset_manifest_ids),
            "information_cutoff": self.information_cutoff.isoformat(),
            "train_period": self.train_period.payload(),
            "validation_period": self.validation_period.payload(),
            "test_period": self.test_period.payload(),
            "forecast_horizon": self.forecast_horizon, "holding_horizon": self.holding_horizon,
            "rebalance_horizon": self.rebalance_horizon, "benchmark_key": self.benchmark_key,
            "benchmark_total_return": self.benchmark_total_return,
            "benchmark_currency": self.benchmark_currency,
            "benchmark_rebalance_horizon": self.benchmark_rebalance_horizon,
            "primary_oos_objective": self.primary_oos_objective,
            "strategic_risk_budget": str(self.strategic_risk_budget),
            "active_risk_budget": str(self.active_risk_budget),
            "tracking_error_budget": str(self.tracking_error_budget),
            "max_stress_loss": str(self.max_stress_loss),
            "risk_aversion_policy_key": self.risk_aversion_policy_key,
            "risk_aversion_min": str(self.risk_aversion_min),
            "risk_aversion_max": str(self.risk_aversion_max),
            "active_risk_aversion_min": str(self.active_risk_aversion_min),
            "active_risk_aversion_max": str(self.active_risk_aversion_max),
            "hard_constraint_policy_key": self.hard_constraint_policy_key,
            "acceptance_thresholds": {key: str(value) for key, value in self.acceptance_thresholds.items()},
            "transaction_cost_model_key": self.transaction_cost_model_key,
            "tax_model_key": self.tax_model_key, "fx_model_key": self.fx_model_key,
            "execution_fidelity_model_key": self.execution_fidelity_model_key,
            "parameter_search_space_key": self.parameter_search_space_key,
            "parameter_search_budget": self.parameter_search_budget,
            "purge_observations": self.purge_observations,
            "embargo_observations": self.embargo_observations,
            "robustness_test_keys": list(self.robustness_test_keys),
            "seed_policy_key": self.seed_policy_key, "code_revision": self.code_revision,
            "parent_spec_hash": self.parent_spec_hash,
        }

    @property
    def spec_hash(self) -> str:
        return digest(canonical(self.payload()))


class BacktestRunStatus(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True, slots=True)
class BacktestRunEvent:
    run_key: str
    spec_hash: str
    status: BacktestRunStatus
    recorded_at: datetime
    evidence_ids: tuple[str, ...]
    output_hash: str | None
    reason_code: str

    def __post_init__(self) -> None:
        _text(self.run_key, "BACKTEST_RUN_EVENT_INVALID")
        if (not isinstance(self.spec_hash, str) or _HASH.fullmatch(self.spec_hash) is None or
                not isinstance(self.status, BacktestRunStatus) or
                (self.output_hash is not None and (not isinstance(self.output_hash, str) or _HASH.fullmatch(self.output_hash) is None))):
            raise InvariantViolation("BACKTEST_RUN_EVENT_INVALID")
        if (self.status is BacktestRunStatus.STARTED) != (self.output_hash is None):
            raise InvariantViolation("BACKTEST_RUN_EVENT_INVALID")
        if self.status is BacktestRunStatus.COMPLETED and self.reason_code != "OK":
            raise InvariantViolation("BACKTEST_RUN_EVENT_INVALID")
        if self.status in {BacktestRunStatus.FAILED, BacktestRunStatus.INTERRUPTED} and self.reason_code == "OK":
            raise InvariantViolation("BACKTEST_RUN_EVENT_INVALID")
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, "BACKTEST_RUN_EVENT_INVALID"))
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "BACKTEST_RUN_EVIDENCE_INVALID"))
        _text(self.reason_code, "BACKTEST_RUN_EVENT_INVALID")

    def payload(self) -> dict[str, object]:
        return {"run_key": self.run_key, "spec_hash": self.spec_hash, "status": self.status.value,
                "recorded_at": self.recorded_at.isoformat(), "evidence_ids": list(self.evidence_ids),
                "output_hash": self.output_hash, "reason_code": self.reason_code}

    @property
    def event_hash(self) -> str:
        return digest(canonical(self.payload()))


class BacktestRunRegistry:
    """One append-only outcome ledger for preregistered validation runs."""

    def __init__(self, mandate_registry: InvestorMandateRegistry) -> None:
        if not isinstance(mandate_registry, InvestorMandateRegistry):
            raise InvariantViolation("BACKTEST_MANDATE_REGISTRY_MISSING")
        self._mandate_registry = mandate_registry
        self._specs: dict[str, BacktestRunSpec] = {}
        self._events: list[BacktestRunEvent] = []

    def _validate_mandate(self, spec: BacktestRunSpec) -> None:
        mandate, benchmark = self._mandate_registry.require_backtest_context(
            spec.mandate_key, benchmark_key=spec.benchmark_key, objective=spec.objective,
            reporting_currency=spec.reporting_currency, at=spec.information_cutoff)
        preference = mandate.risk_preference
        if (spec.benchmark_total_return is not benchmark.total_return or
                spec.benchmark_currency != benchmark.currency or
                spec.benchmark_rebalance_horizon != benchmark.rebalance_horizon or
                spec.rebalance_horizon != mandate.rebalance_horizon or
                (spec.strategic_risk_budget, spec.active_risk_budget, spec.tracking_error_budget,
                 spec.max_stress_loss, spec.risk_aversion_min, spec.risk_aversion_max,
                 spec.active_risk_aversion_min, spec.active_risk_aversion_max) !=
                (preference.strategic_risk_budget, preference.active_risk_budget, preference.tracking_error_budget,
                 preference.max_stress_loss, preference.risk_aversion_min, preference.risk_aversion_max,
                 preference.active_risk_aversion_min, preference.active_risk_aversion_max)):
            raise InvariantViolation("BACKTEST_RUN_SPEC_MANDATE_MISMATCH")

    def preregister(self, spec: BacktestRunSpec) -> str:
        if not isinstance(spec, BacktestRunSpec):
            raise InvariantViolation("BACKTEST_RUN_SPEC_INVALID")
        self._validate_mandate(spec)
        if spec.parent_spec_hash is not None and spec.parent_spec_hash not in {item.spec_hash for item in self._specs.values()}:
            raise InvariantViolation("BACKTEST_PARENT_SPEC_UNKNOWN")
        previous = self._specs.get(spec.key)
        if previous is not None and previous != spec:
            raise InvariantViolation("BACKTEST_RUN_SPEC_CONFLICT")
        self._specs[spec.key] = spec
        return spec.spec_hash

    def start(self, run_key: str, *, at: datetime, evidence_ids: tuple[str, ...]) -> BacktestRunEvent:
        spec = self._require_spec(run_key)
        if self._events_for(run_key):
            raise InvariantViolation("BACKTEST_RUN_ALREADY_STARTED")
        event = BacktestRunEvent(run_key, spec.spec_hash, BacktestRunStatus.STARTED, at, evidence_ids, None, "PREREGISTERED")
        self._events.append(event)
        return event

    def record_outcome(self, run_key: str, *, status: BacktestRunStatus, at: datetime,
                       evidence_ids: tuple[str, ...], output_hash: str, reason_code: str) -> BacktestRunEvent:
        spec = self._require_spec(run_key)
        events = self._events_for(run_key)
        if not events or events[-1].status is not BacktestRunStatus.STARTED:
            raise InvariantViolation("BACKTEST_RUN_NOT_ACTIVE")
        event = BacktestRunEvent(run_key, spec.spec_hash, status, at, evidence_ids, output_hash, reason_code)
        if event.status is BacktestRunStatus.STARTED or event.recorded_at < events[-1].recorded_at:
            raise InvariantViolation("BACKTEST_RUN_OUTCOME_INVALID")
        self._events.append(event)
        return event

    def _require_spec(self, run_key: str) -> BacktestRunSpec:
        try:
            return self._specs[run_key]
        except KeyError:
            raise InvariantViolation("BACKTEST_RUN_NOT_PREREGISTERED") from None

    def _events_for(self, run_key: str) -> tuple[BacktestRunEvent, ...]:
        return tuple(event for event in self._events if event.run_key == run_key)

    @property
    def registry_hash(self) -> str:
        return digest(canonical(self._body()))

    def _body(self) -> dict[str, object]:
        return {
            "specs": [self._specs[key].payload() | {"spec_hash": self._specs[key].spec_hash}
                      for key in sorted(self._specs)],
            "events": [event.payload() | {"event_hash": event.event_hash} for event in self._events],
        }

    def payload(self) -> dict[str, object]:
        return self._body() | {"registry_hash": self.registry_hash}

    def publish(self, store: ImmutableDatasetStore) -> str:
        return store.catalog("backtest-run-registry", self.payload())
