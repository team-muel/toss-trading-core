"""Versioned, immutable portfolio-decision records and post-horizon outcome events."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.domain.economics import (
    ReturnSemanticType, ReturnMetricStatus, ReturnUnit, RiskContributionType,
    RETURN_UNITS as _UNITS,
)
from asset_management.governance import InvestorMandateRegistry, MandateObjective
from asset_management.portfolio.models import PortfolioTarget
from asset_management.risk.models import CurrencyBasis
from asset_management.states.models import StateType

from .governor import DecisionState


DECISION_JOURNAL_SCHEMA_VERSION = "decision-economic-journal@1"
_HASH = re.compile(r"[0-9a-f]{64}")


def _text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvariantViolation(reason)
    return value


def _utc(value: object, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


def _decimal(value: object, reason: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantViolation(reason)
    return value


def _ids(values: object, reason: str, *, required: bool = True) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (required and not values) or len(values) != len(set(values)):
        raise InvariantViolation(reason)
    return tuple(sorted(_text(item, reason) for item in values))


def _text_map(values: object, reason: str, *, required: bool = True) -> Mapping[str, str]:
    if not isinstance(values, Mapping) or (required and not values):
        raise InvariantViolation(reason)
    result = {_text(key, reason): _text(value, reason) for key, value in values.items()}
    return MappingProxyType(dict(sorted(result.items())))


def _decimal_map(values: object, reason: str) -> Mapping[str, Decimal]:
    if not isinstance(values, Mapping) or not values:
        raise InvariantViolation(reason)
    result = {_text(key, reason): _decimal(value, reason) for key, value in values.items()}
    return MappingProxyType(dict(sorted(result.items())))


def _target(value: object, reason: str) -> PortfolioTarget:
    if (not isinstance(value, PortfolioTarget) or not value.stage or any(weight < 0 for weight in value.weights)
            or sum(value.weights) != Decimal(1)):
        raise InvariantViolation(reason)
    return value


def _target_payload(target: PortfolioTarget) -> dict[str, object]:
    return {"instruments": list(target.instruments), "weights": [str(item) for item in target.weights],
            "stage": target.stage, "reason_codes": list(target.reason_codes)}


class DecisionQuality(StrEnum):
    NOT_MATURED = "NOT_MATURED"
    PENDING_OUTCOME = "PENDING_OUTCOME"
    GOOD_DECISION_GOOD_OUTCOME = "GOOD_DECISION_GOOD_OUTCOME"
    GOOD_DECISION_BAD_OUTCOME = "GOOD_DECISION_BAD_OUTCOME"
    BAD_DECISION_GOOD_OUTCOME = "BAD_DECISION_GOOD_OUTCOME"
    BAD_DECISION_BAD_OUTCOME = "BAD_DECISION_BAD_OUTCOME"


@dataclass(frozen=True, slots=True)
class ReturnMetric:
    semantic_type: ReturnSemanticType
    status: ReturnMetricStatus
    value: Decimal | None
    currency_basis: CurrencyBasis
    forecast_horizon: int
    unit: ReturnUnit
    formula_version: str
    model_version: str

    def __post_init__(self) -> None:
        if (not isinstance(self.semantic_type, ReturnSemanticType) or
                not isinstance(self.status, ReturnMetricStatus) or
                not isinstance(self.currency_basis, CurrencyBasis) or
                not isinstance(self.unit, ReturnUnit) or self.unit is not _UNITS[self.semantic_type] or
                type(self.forecast_horizon) is not int or self.forecast_horizon not in {21, 63, 126, 252}):
            raise InvariantViolation("DECISION_RETURN_SEMANTICS_INVALID")
        _text(self.formula_version, "DECISION_RETURN_SEMANTICS_INVALID")
        _text(self.model_version, "DECISION_RETURN_SEMANTICS_INVALID")
        if self.status is ReturnMetricStatus.AVAILABLE:
            _decimal(self.value, "DECISION_RETURN_SEMANTICS_INVALID")
        elif self.value is not None:
            raise InvariantViolation("DECISION_RETURN_STATUS_VALUE_INVALID")

    def payload(self) -> dict[str, object]:
        return {"semantic_type": self.semantic_type.value, "status": self.status.value,
                "value": None if self.value is None else str(self.value),
                "currency_basis": self.currency_basis.value, "forecast_horizon": self.forecast_horizon,
                "unit": self.unit.value, "formula_version": self.formula_version,
                "model_version": self.model_version}

    @classmethod
    def from_payload(cls, raw: object) -> ReturnMetric:
        if not isinstance(raw, Mapping):
            raise InvariantViolation("DECISION_RETURN_RECORD_INVALID")
        try:
            metric = cls(ReturnSemanticType(raw["semantic_type"]), ReturnMetricStatus(raw["status"]),
                         None if raw["value"] is None else Decimal(raw["value"]),
                         CurrencyBasis(raw["currency_basis"]), raw["forecast_horizon"],
                         ReturnUnit(raw["unit"]), raw["formula_version"], raw["model_version"])
            if dict(raw) != metric.payload():
                raise ValueError
            return metric
        except (KeyError, TypeError, ValueError, InvalidOperation, InvariantViolation) as error:
            raise InvariantViolation("DECISION_RETURN_RECORD_INVALID") from error


def _metrics(values: object, *, objective: MandateObjective, pretrade: bool) -> tuple[ReturnMetric, ...]:
    if not isinstance(values, tuple) or len(values) != len(ReturnSemanticType):
        raise InvariantViolation("DECISION_RETURN_SET_INVALID")
    if any(not isinstance(item, ReturnMetric) for item in values):
        raise InvariantViolation("DECISION_RETURN_SET_INVALID")
    result = tuple(sorted(values, key=lambda item: item.semantic_type.value))
    if {item.semantic_type for item in result} != set(ReturnSemanticType):
        raise InvariantViolation("DECISION_RETURN_SET_INVALID")
    by_type = {item.semantic_type: item for item in result}
    required = (ReturnSemanticType.PRICING_BASELINE_RETURN,
                ReturnSemanticType.FORECAST_TOTAL_RETURN_GROSS,
                ReturnSemanticType.FORECAST_TOTAL_RETURN_NET)
    if any(by_type[item].status is not ReturnMetricStatus.AVAILABLE for item in required):
        raise InvariantViolation("DECISION_RETURN_REQUIRED_VALUE_MISSING")
    if by_type[ReturnSemanticType.FORECAST_TOTAL_RETURN_NET].value > by_type[ReturnSemanticType.FORECAST_TOTAL_RETURN_GROSS].value:
        raise InvariantViolation("DECISION_FORECAST_GROSS_NET_INVALID")
    expected_active = by_type[ReturnSemanticType.EXPECTED_BENCHMARK_ACTIVE_RETURN]
    if ((objective is MandateObjective.ABSOLUTE_WEALTH and expected_active.status is not ReturnMetricStatus.NOT_APPLICABLE) or
            (objective is not MandateObjective.ABSOLUTE_WEALTH and expected_active.status is not ReturnMetricStatus.AVAILABLE)):
        raise InvariantViolation("DECISION_BENCHMARK_RETURN_SEMANTICS_INVALID")
    if by_type[ReturnSemanticType.MODEL_RELATIVE_ALPHA].status not in {
            ReturnMetricStatus.AVAILABLE, ReturnMetricStatus.NOT_APPLICABLE}:
        raise InvariantViolation("DECISION_MODEL_ALPHA_SEMANTICS_INVALID")
    if pretrade and any(by_type[item].status is not ReturnMetricStatus.NOT_MATURED for item in (
            ReturnSemanticType.REALIZED_ACTIVE_RETURN, ReturnSemanticType.REGRESSION_ALPHA)):
        raise InvariantViolation("DECISION_OUTCOME_RETURN_PREMATURE")
    return result


@dataclass(frozen=True, slots=True)
class EconomicDecisionRecord:
    run_id: str
    as_of: datetime
    information_cutoff: datetime
    assessment_horizon_end: datetime
    mandate_key: str
    mandate_version: str
    benchmark_key: str
    benchmark_version: str
    objective: MandateObjective
    reporting_currency: str
    risk_budget_version: str
    risk_aversion_policy_version: str
    state_snapshot_ids: Mapping[StateType, str]
    pricing_lineage_ids: tuple[str, ...]
    forecast_lineage_ids: tuple[str, ...]
    risk_lineage_ids: tuple[str, ...]
    target_lineage_ids: tuple[str, ...]
    decision_lineage_ids: tuple[str, ...]
    calculation_lineage_ids: tuple[str, ...]
    return_metrics: tuple[ReturnMetric, ...]
    risk_snapshot_id: str
    risk_values: Mapping[str, Decimal]
    risk_contribution_type: RiskContributionType
    risk_contributions: Mapping[str, Decimal]
    raw_target: PortfolioTarget
    constrained_target: PortfolioTarget
    executable_target: PortfolioTarget
    risk_decision_id: str
    risk_decision_state: DecisionState
    risk_reason_codes: tuple[str, ...]
    policy_versions: Mapping[str, str]
    parameter_versions: Mapping[str, str]
    model_versions: Mapping[str, str]
    code_revision: str
    decision_id: str | None = None
    content_hash: str | None = None

    def __post_init__(self) -> None:
        for value in (self.run_id, self.mandate_key, self.mandate_version, self.benchmark_key,
                      self.benchmark_version, self.reporting_currency, self.risk_budget_version, self.risk_aversion_policy_version,
                      self.risk_snapshot_id, self.risk_decision_id,
                      self.code_revision):
            _text(value, "ECONOMIC_DECISION_FIELD_INVALID")
        if (not isinstance(self.objective, MandateObjective) or not isinstance(self.risk_decision_state, DecisionState)
                or not isinstance(self.risk_contribution_type, RiskContributionType)):
            raise InvariantViolation("ECONOMIC_DECISION_FIELD_INVALID")
        as_of = _utc(self.as_of, "ECONOMIC_DECISION_TIME_INVALID")
        cutoff = _utc(self.information_cutoff, "ECONOMIC_DECISION_TIME_INVALID")
        maturity = _utc(self.assessment_horizon_end, "ECONOMIC_DECISION_TIME_INVALID")
        if cutoff > as_of or maturity <= as_of:
            raise InvariantViolation("ECONOMIC_DECISION_TIME_ORDER_INVALID")
        if not isinstance(self.state_snapshot_ids, Mapping) or set(self.state_snapshot_ids) != set(StateType):
            raise InvariantViolation("ECONOMIC_DECISION_STATE_SET_INVALID")
        states = MappingProxyType(dict(sorted(
            ((item.value, _text(value, "ECONOMIC_DECISION_STATE_SET_INVALID"))
            for item, value in self.state_snapshot_ids.items()))))
        raw = _target(self.raw_target, "ECONOMIC_DECISION_TARGET_INVALID")
        constrained = _target(self.constrained_target, "ECONOMIC_DECISION_TARGET_INVALID")
        executable = _target(self.executable_target, "ECONOMIC_DECISION_TARGET_INVALID")
        if len({item.instruments for item in (raw, constrained, executable)}) != 1:
            raise InvariantViolation("ECONOMIC_DECISION_TARGET_UNIVERSE_INVALID")
        object.__setattr__(self, "pricing_lineage_ids", _ids(self.pricing_lineage_ids, "ECONOMIC_DECISION_LINEAGE_INVALID"))
        object.__setattr__(self, "forecast_lineage_ids", _ids(self.forecast_lineage_ids, "ECONOMIC_DECISION_LINEAGE_INVALID"))
        object.__setattr__(self, "risk_lineage_ids", _ids(self.risk_lineage_ids, "ECONOMIC_DECISION_LINEAGE_INVALID"))
        object.__setattr__(self, "target_lineage_ids", _ids(self.target_lineage_ids, "ECONOMIC_DECISION_LINEAGE_INVALID"))
        object.__setattr__(self, "decision_lineage_ids", _ids(self.decision_lineage_ids, "ECONOMIC_DECISION_LINEAGE_INVALID"))
        object.__setattr__(self, "calculation_lineage_ids", _ids(self.calculation_lineage_ids, "ECONOMIC_DECISION_LINEAGE_INVALID"))
        object.__setattr__(self, "return_metrics", _metrics(self.return_metrics, objective=self.objective, pretrade=True))
        object.__setattr__(self, "risk_values", _decimal_map(self.risk_values, "ECONOMIC_DECISION_RISK_INVALID"))
        object.__setattr__(self, "risk_contributions", _decimal_map(
            self.risk_contributions, "ECONOMIC_DECISION_RISK_CONTRIBUTION_INVALID"))
        object.__setattr__(self, "risk_reason_codes", _ids(
            self.risk_reason_codes, "ECONOMIC_DECISION_RISK_REASONS_INVALID", required=False))
        object.__setattr__(self, "policy_versions", _text_map(self.policy_versions, "ECONOMIC_DECISION_VERSION_INVALID"))
        object.__setattr__(self, "parameter_versions", _text_map(self.parameter_versions, "ECONOMIC_DECISION_VERSION_INVALID"))
        object.__setattr__(self, "model_versions", _text_map(self.model_versions, "ECONOMIC_DECISION_VERSION_INVALID"))
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "information_cutoff", cutoff)
        object.__setattr__(self, "assessment_horizon_end", maturity)
        object.__setattr__(self, "state_snapshot_ids", states)
        body_hash = digest(canonical(self.body()))
        identifier = f"decision-{body_hash}"
        if self.decision_id is not None and self.decision_id != identifier:
            raise InvariantViolation("ECONOMIC_DECISION_ID_INVALID")
        if self.content_hash is not None and self.content_hash != body_hash:
            raise InvariantViolation("ECONOMIC_DECISION_CONTENT_HASH_INVALID")
        object.__setattr__(self, "decision_id", identifier)
        object.__setattr__(self, "content_hash", body_hash)

    def body(self) -> dict[str, object]:
        values = lambda source: {key: str(value) for key, value in source.items()}
        return {"schema_version": DECISION_JOURNAL_SCHEMA_VERSION, "run_id": self.run_id,
                "as_of": self.as_of.isoformat(), "information_cutoff": self.information_cutoff.isoformat(),
                "assessment_horizon_end": self.assessment_horizon_end.isoformat(), "mandate_key": self.mandate_key,
                "mandate_version": self.mandate_version, "benchmark_key": self.benchmark_key,
                "benchmark_version": self.benchmark_version, "objective": self.objective.value,
                "reporting_currency": self.reporting_currency,
                "risk_budget_version": self.risk_budget_version,
                "risk_aversion_policy_version": self.risk_aversion_policy_version,
                "state_snapshot_ids": dict(self.state_snapshot_ids),
                "pricing_lineage_ids": list(self.pricing_lineage_ids),
                "forecast_lineage_ids": list(self.forecast_lineage_ids), "risk_lineage_ids": list(self.risk_lineage_ids),
                "target_lineage_ids": list(self.target_lineage_ids), "decision_lineage_ids": list(self.decision_lineage_ids),
                "calculation_lineage_ids": list(self.calculation_lineage_ids),
                "return_metrics": [item.payload() for item in self.return_metrics],
                "risk_snapshot_id": self.risk_snapshot_id, "risk_values": values(self.risk_values),
                "risk_contribution_type": self.risk_contribution_type.value,
                "risk_contributions": values(self.risk_contributions), "raw_target": _target_payload(self.raw_target),
                "constrained_target": _target_payload(self.constrained_target),
                "executable_target": _target_payload(self.executable_target),
                "risk_decision_id": self.risk_decision_id, "risk_decision_state": self.risk_decision_state.value,
                "risk_reason_codes": list(self.risk_reason_codes), "policy_versions": dict(self.policy_versions),
                "parameter_versions": dict(self.parameter_versions), "model_versions": dict(self.model_versions),
                "code_revision": self.code_revision}

    def payload(self) -> dict[str, object]:
        return self.body() | {"decision_id": self.decision_id, "content_hash": self.content_hash}

    @classmethod
    def from_payload(cls, raw: object) -> EconomicDecisionRecord:
        if not isinstance(raw, Mapping) or raw.get("schema_version") != DECISION_JOURNAL_SCHEMA_VERSION:
            raise InvariantViolation("ECONOMIC_DECISION_SCHEMA_VERSION_UNSUPPORTED")
        try:
            target = lambda item: PortfolioTarget(tuple(item["instruments"]), tuple(Decimal(value) for value in item["weights"]), item["stage"], tuple(item["reason_codes"]))
            record = cls(
                raw["run_id"], datetime.fromisoformat(raw["as_of"]), datetime.fromisoformat(raw["information_cutoff"]),
                datetime.fromisoformat(raw["assessment_horizon_end"]), raw["mandate_key"], raw["mandate_version"],
                raw["benchmark_key"], raw["benchmark_version"], MandateObjective(raw["objective"]), raw["reporting_currency"], raw["risk_budget_version"],
                raw["risk_aversion_policy_version"], {StateType(key): value for key, value in raw["state_snapshot_ids"].items()},
                tuple(raw["pricing_lineage_ids"]), tuple(raw["forecast_lineage_ids"]), tuple(raw["risk_lineage_ids"]),
                tuple(raw["target_lineage_ids"]), tuple(raw["decision_lineage_ids"]), tuple(raw["calculation_lineage_ids"]),
                tuple(ReturnMetric.from_payload(item) for item in raw["return_metrics"]), raw["risk_snapshot_id"],
                {key: Decimal(value) for key, value in raw["risk_values"].items()}, RiskContributionType(raw["risk_contribution_type"]),
                {key: Decimal(value) for key, value in raw["risk_contributions"].items()}, target(raw["raw_target"]),
                target(raw["constrained_target"]), target(raw["executable_target"]), raw["risk_decision_id"],
                DecisionState(raw["risk_decision_state"]), tuple(raw["risk_reason_codes"]), raw["policy_versions"],
                raw["parameter_versions"], raw["model_versions"], raw["code_revision"], raw["decision_id"], raw["content_hash"])
            if dict(raw) != record.payload():
                raise ValueError
            return record
        except (KeyError, TypeError, ValueError, InvalidOperation, InvariantViolation) as error:
            raise InvariantViolation("ECONOMIC_DECISION_RECORD_INVALID") from error


def classify_decision_quality(*, process_good: bool, outcome_good: bool) -> DecisionQuality:
    if type(process_good) is not bool or type(outcome_good) is not bool:
        raise InvariantViolation("DECISION_QUALITY_INPUT_INVALID")
    return {(True, True): DecisionQuality.GOOD_DECISION_GOOD_OUTCOME,
            (True, False): DecisionQuality.GOOD_DECISION_BAD_OUTCOME,
            (False, True): DecisionQuality.BAD_DECISION_GOOD_OUTCOME,
            (False, False): DecisionQuality.BAD_DECISION_BAD_OUTCOME}[(process_good, outcome_good)]


@dataclass(frozen=True, slots=True)
class DecisionOutcomeEvent:
    decision_id: str
    decision_content_hash: str
    assessed_at: datetime
    realized_active_return: ReturnMetric
    regression_alpha: ReturnMetric
    process_good: bool
    outcome_good: bool
    quality: DecisionQuality
    outcome_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.decision_id, "DECISION_OUTCOME_FIELD_INVALID")
        if not isinstance(self.decision_content_hash, str) or _HASH.fullmatch(self.decision_content_hash) is None:
            raise InvariantViolation("DECISION_OUTCOME_FIELD_INVALID")
        if (not isinstance(self.realized_active_return, ReturnMetric) or
                self.realized_active_return.semantic_type is not ReturnSemanticType.REALIZED_ACTIVE_RETURN or
                not isinstance(self.regression_alpha, ReturnMetric) or
                self.regression_alpha.semantic_type is not ReturnSemanticType.REGRESSION_ALPHA or
                self.realized_active_return.status is ReturnMetricStatus.NOT_MATURED or
                self.regression_alpha.status is ReturnMetricStatus.NOT_MATURED or
                self.quality is not classify_decision_quality(process_good=self.process_good, outcome_good=self.outcome_good)):
            raise InvariantViolation("DECISION_OUTCOME_SEMANTICS_INVALID")
        assessed = _utc(self.assessed_at, "DECISION_OUTCOME_TIME_INVALID")
        object.__setattr__(self, "assessed_at", assessed)
        body_hash = digest(canonical(self.body()))
        identifier = f"outcome-{body_hash}"
        if self.outcome_id is not None and self.outcome_id != identifier:
            raise InvariantViolation("DECISION_OUTCOME_ID_INVALID")
        object.__setattr__(self, "outcome_id", identifier)

    def body(self) -> dict[str, object]:
        return {"schema_version": DECISION_JOURNAL_SCHEMA_VERSION, "decision_id": self.decision_id,
                "decision_content_hash": self.decision_content_hash, "assessed_at": self.assessed_at.isoformat(),
                "realized_active_return": self.realized_active_return.payload(),
                "regression_alpha": self.regression_alpha.payload(), "process_good": self.process_good,
                "outcome_good": self.outcome_good, "quality": self.quality.value}

    def payload(self) -> dict[str, object]:
        return self.body() | {"outcome_id": self.outcome_id}

    @classmethod
    def from_payload(cls, raw: object) -> DecisionOutcomeEvent:
        if not isinstance(raw, Mapping) or raw.get("schema_version") != DECISION_JOURNAL_SCHEMA_VERSION:
            raise InvariantViolation("DECISION_OUTCOME_SCHEMA_VERSION_UNSUPPORTED")
        try:
            event = cls(raw["decision_id"], raw["decision_content_hash"], datetime.fromisoformat(raw["assessed_at"]),
                        ReturnMetric.from_payload(raw["realized_active_return"]),
                        ReturnMetric.from_payload(raw["regression_alpha"]), raw["process_good"], raw["outcome_good"],
                        DecisionQuality(raw["quality"]), raw["outcome_id"])
            if dict(raw) != event.payload():
                raise ValueError
            return event
        except (KeyError, TypeError, ValueError, InvalidOperation, InvariantViolation) as error:
            raise InvariantViolation("DECISION_OUTCOME_RECORD_INVALID") from error


def _validate_outcome(record: EconomicDecisionRecord, event: DecisionOutcomeEvent) -> None:
    if event.decision_content_hash != record.content_hash:
        raise InvariantViolation("DECISION_OUTCOME_DECISION_MISMATCH")
    if event.assessed_at < record.assessment_horizon_end:
        raise InvariantViolation("DECISION_OUTCOME_NOT_MATURED")
    previous_metrics = {item.semantic_type: item for item in record.return_metrics}
    for metric in (event.realized_active_return, event.regression_alpha):
        baseline = previous_metrics[metric.semantic_type]
        if (baseline.status is not ReturnMetricStatus.NOT_MATURED or
                tuple(metric.payload()[key] for key in ("currency_basis", "forecast_horizon", "unit", "formula_version", "model_version")) !=
                tuple(baseline.payload()[key] for key in ("currency_basis", "forecast_horizon", "unit", "formula_version", "model_version"))):
            raise InvariantViolation("DECISION_OUTCOME_SEMANTIC_MISMATCH")
    if ((record.objective is MandateObjective.ABSOLUTE_WEALTH and
         event.realized_active_return.status is not ReturnMetricStatus.NOT_APPLICABLE) or
            (record.objective is not MandateObjective.ABSOLUTE_WEALTH and
             event.realized_active_return.status is not ReturnMetricStatus.AVAILABLE)):
        raise InvariantViolation("DECISION_OUTCOME_BENCHMARK_SEMANTICS_INVALID")


class EconomicDecisionJournal:
    """Append-only JSONL decision and outcome events with deterministic replay validation."""

    def __init__(self, path: str | Path, mandate_registry: InvestorMandateRegistry) -> None:
        self.path = Path(path)
        if not isinstance(mandate_registry, InvestorMandateRegistry):
            raise InvariantViolation("ECONOMIC_DECISION_MANDATE_REGISTRY_MISSING")
        self._mandate_registry = mandate_registry

    def append(self, record: EconomicDecisionRecord) -> None:
        if not isinstance(record, EconomicDecisionRecord):
            raise InvariantViolation("ECONOMIC_DECISION_RECORD_INVALID")
        self._validate_authority(record)
        records, _ = self._load()
        previous = {item.decision_id: item for item in records}.get(record.decision_id)
        if previous is not None:
            if previous != record:
                raise InvariantViolation("ECONOMIC_DECISION_OVERWRITE")
            return
        self._write("DECISION_RECORDED", record.payload())

    def append_outcome(self, event: DecisionOutcomeEvent) -> None:
        if not isinstance(event, DecisionOutcomeEvent):
            raise InvariantViolation("DECISION_OUTCOME_RECORD_INVALID")
        records, outcomes = self._load()
        record = {item.decision_id: item for item in records}.get(event.decision_id)
        if record is None:
            raise InvariantViolation("DECISION_OUTCOME_DECISION_UNKNOWN")
        _validate_outcome(record, event)
        previous = {item.decision_id: item for item in outcomes}.get(event.decision_id)
        if previous is not None:
            if previous != event:
                raise InvariantViolation("DECISION_OUTCOME_OVERWRITE")
            return
        self._write("DECISION_OUTCOME_RECORDED", event.payload())

    def quality_at(self, decision_id: str, *, at: datetime) -> DecisionQuality:
        records, outcomes = self._load()
        record = {item.decision_id: item for item in records}.get(_text(decision_id, "ECONOMIC_DECISION_ID_INVALID"))
        if record is None:
            raise InvariantViolation("ECONOMIC_DECISION_UNKNOWN")
        instant = _utc(at, "ECONOMIC_DECISION_TIME_INVALID")
        if instant < record.assessment_horizon_end:
            return DecisionQuality.NOT_MATURED
        outcome = next((item for item in outcomes if item.decision_id == record.decision_id), None)
        return DecisionQuality.PENDING_OUTCOME if outcome is None else outcome.quality

    def records(self) -> tuple[EconomicDecisionRecord, ...]:
        return self._load()[0]

    def outcomes(self) -> tuple[DecisionOutcomeEvent, ...]:
        return self._load()[1]

    def _load(self) -> tuple[tuple[EconomicDecisionRecord, ...], tuple[DecisionOutcomeEvent, ...]]:
        if not self.path.exists():
            return (), ()
        records: list[EconomicDecisionRecord] = []
        outcomes: list[DecisionOutcomeEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
                if set(raw) != {"event_type", "payload"}:
                    raise ValueError
                if raw["event_type"] == "DECISION_RECORDED":
                    record = EconomicDecisionRecord.from_payload(raw["payload"])
                    if record.decision_id in {item.decision_id for item in records}:
                        raise InvariantViolation("ECONOMIC_DECISION_DUPLICATE")
                    self._validate_authority(record)
                    records.append(record)
                elif raw["event_type"] == "DECISION_OUTCOME_RECORDED":
                    outcome = DecisionOutcomeEvent.from_payload(raw["payload"])
                    if outcome.decision_id in {item.decision_id for item in outcomes}:
                        raise InvariantViolation("DECISION_OUTCOME_DUPLICATE")
                    record = next((item for item in records if item.decision_id == outcome.decision_id), None)
                    if record is None:
                        raise InvariantViolation("DECISION_OUTCOME_DECISION_UNKNOWN")
                    _validate_outcome(record, outcome)
                    outcomes.append(outcome)
                else:
                    raise ValueError
            except (TypeError, ValueError, KeyError, InvariantViolation) as error:
                raise InvariantViolation("ECONOMIC_DECISION_JOURNAL_RECORD_INVALID") from error
        return tuple(records), tuple(outcomes)

    def _validate_authority(self, record: EconomicDecisionRecord) -> None:
        mandate, benchmark = self._mandate_registry.require_backtest_context(
            record.mandate_key, benchmark_key=record.benchmark_key, objective=record.objective,
            reporting_currency=record.reporting_currency, at=record.as_of)
        if mandate.version != record.mandate_version or benchmark.version != record.benchmark_version:
            raise InvariantViolation("ECONOMIC_DECISION_MANDATE_VERSION_MISMATCH")

    def _write(self, event_type: str, payload: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"event_type": event_type, "payload": payload}, ensure_ascii=False,
                                    sort_keys=True, separators=(",", ":")))
            stream.write("\n")
