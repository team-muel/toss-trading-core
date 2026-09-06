"""Versioned, point-in-time signal contracts distinct from Feature values."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.domain.horizon import SignalValidity
from asset_management.features.models import FeatureSnapshot
from asset_management.quality.models import QualityStatus


class SignalDirectionality(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class SignalType(StrEnum):
    CROSS_SECTIONAL = "CROSS_SECTIONAL"
    TIME_SERIES = "TIME_SERIES"


class CostSensitivity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


def _identifiers(values: tuple[str, ...], reason: str) -> tuple[str, ...]:
    if (not values or len(values) != len(set(values)) or
            any(not isinstance(value, str) or not value.strip() for value in values)):
        raise InvariantViolation(reason)
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class SignalDefinition:
    signal_id: str
    version: str
    name: str
    economic_rationale: str
    source_feature_ids: tuple[str, ...]
    transform_rule: str
    ranking_rule: str
    normalization_rule: str
    neutralization_rule: str
    directionality: SignalDirectionality
    signal_type: SignalType
    validity: SignalValidity
    universe_id: str
    currency: str
    minimum_coverage: Decimal
    minimum_history: int
    missing_policy: str
    outlier_policy: str
    expected_turnover: Decimal
    cost_sensitivity: CostSensitivity
    formula_version: str
    parameter_set_id: str

    def __post_init__(self) -> None:
        text = (self.signal_id, self.version, self.name, self.economic_rationale,
                self.transform_rule, self.ranking_rule, self.normalization_rule,
                self.neutralization_rule, self.universe_id, self.currency,
                self.missing_policy, self.outlier_policy, self.formula_version,
                self.parameter_set_id)
        if (any(not isinstance(value, str) or not value.strip() for value in text) or
                not re.fullmatch(r"[a-z][a-z0-9_.-]*", self.signal_id) or
                not isinstance(self.directionality, SignalDirectionality) or
                not isinstance(self.signal_type, SignalType) or
                not isinstance(self.cost_sensitivity, CostSensitivity) or
                not isinstance(self.validity, SignalValidity) or
                not re.fullmatch(r"[A-Z]{3}", self.currency) or
                not isinstance(self.minimum_history, int) or self.minimum_history <= 0 or
                not self.minimum_coverage.is_finite() or
                not Decimal(0) < self.minimum_coverage <= Decimal(1) or
                not self.expected_turnover.is_finite() or
                not Decimal(0) <= self.expected_turnover <= Decimal(1)):
            raise InvariantViolation("SIGNAL_DEFINITION_INVALID")
        object.__setattr__(self, "source_feature_ids", _identifiers(
            self.source_feature_ids, "SIGNAL_SOURCE_FEATURES_INVALID"))

    @property
    def key(self) -> str:
        return f"{self.signal_id}@{self.version}"

    def payload(self) -> dict[str, object]:
        return {
            "signal_id": self.signal_id, "version": self.version, "name": self.name,
            "economic_rationale": self.economic_rationale,
            "source_feature_ids": list(self.source_feature_ids),
            "transform_rule": self.transform_rule, "ranking_rule": self.ranking_rule,
            "normalization_rule": self.normalization_rule,
            "neutralization_rule": self.neutralization_rule,
            "directionality": self.directionality.value, "signal_type": self.signal_type.value,
            "validity": self.validity.payload(), "universe_id": self.universe_id,
            "currency": self.currency, "minimum_coverage": str(self.minimum_coverage),
            "minimum_history": self.minimum_history, "missing_policy": self.missing_policy,
            "outlier_policy": self.outlier_policy,
            "expected_turnover": str(self.expected_turnover),
            "cost_sensitivity": self.cost_sensitivity.value,
            "formula_version": self.formula_version,
            "parameter_set_id": self.parameter_set_id,
        }


@dataclass(frozen=True, slots=True)
class SignalContext:
    as_of: datetime
    information_cutoff: datetime
    universe_manifest_id: str
    historical_universe: tuple[str, ...]
    history_feature_manifest_ids: tuple[str, ...]
    code_revision: str

    def __post_init__(self) -> None:
        if (self.as_of.tzinfo is None or self.as_of.utcoffset() is None or
                self.information_cutoff.tzinfo is None or self.information_cutoff.utcoffset() is None):
            raise InvariantViolation("SIGNAL_CONTEXT_TIME_NOT_AWARE")
        if self.information_cutoff > self.as_of:
            raise InvariantViolation("SIGNAL_CONTEXT_CUTOFF_AFTER_AS_OF")
        if (not re.fullmatch(r"[0-9a-f]{64}", self.universe_manifest_id) or
                not re.fullmatch(r"git:[0-9a-f]{7,40}", self.code_revision)):
            raise InvariantViolation("SIGNAL_CONTEXT_LINEAGE_INVALID")
        object.__setattr__(self, "historical_universe", _identifiers(
            self.historical_universe, "SIGNAL_CONTEXT_UNIVERSE_INVALID"))
        history = _identifiers(self.history_feature_manifest_ids, "SIGNAL_CONTEXT_HISTORY_INVALID")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in history):
            raise InvariantViolation("SIGNAL_CONTEXT_HISTORY_INVALID")
        object.__setattr__(self, "history_feature_manifest_ids", history)
        object.__setattr__(self, "as_of", self.as_of.astimezone(timezone.utc))
        object.__setattr__(self, "information_cutoff", self.information_cutoff.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class SignalFeatureInput:
    snapshot: FeatureSnapshot
    manifest_id: str

    def __post_init__(self) -> None:
        if (not isinstance(self.snapshot, FeatureSnapshot) or
                not re.fullmatch(r"[0-9a-f]{64}", self.manifest_id)):
            raise InvariantViolation("SIGNAL_FEATURE_INPUT_INVALID")


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    signal_run_id: str
    signal_id: str
    signal_version: str
    semantic_type: str
    as_of: str
    information_cutoff: str
    values: Mapping[str, str | None]
    quality_status: str
    coverage: str
    source_feature_manifest_ids: tuple[str, ...]
    history_feature_manifest_ids: tuple[str, ...]
    universe_manifest_id: str
    formula_version: str
    parameter_set_id: str
    code_revision: str
    validity: SignalValidity
    output_hash: str

    def __post_init__(self) -> None:
        if (self.semantic_type != "SIGNAL_VALUE" or not re.fullmatch(r"[0-9a-f]{64}", self.signal_run_id) or
                not re.fullmatch(r"[0-9a-f]{64}", self.output_hash) or
                not isinstance(self.validity, SignalValidity) or not self.values or
                self.quality_status not in {QualityStatus.VALID.value, QualityStatus.ESTIMATED.value} or
                not re.fullmatch(r"[0-9a-f]{64}", self.universe_manifest_id) or
                not re.fullmatch(r"git:[0-9a-f]{7,40}", self.code_revision)):
            raise InvariantViolation("SIGNAL_SNAPSHOT_INVALID")
        try:
            coverage = Decimal(self.coverage)
            values = dict(sorted(self.values.items()))
            if (not coverage.is_finite() or not Decimal(0) < coverage <= Decimal(1) or
                    any(value is not None and not Decimal(value).is_finite()
                        for value in values.values()) or
                    digest(canonical(values)) != self.output_hash):
                raise ValueError
            for value in (self.as_of, self.information_cutoff):
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError
        except (TypeError, ValueError, ArithmeticError):
            raise InvariantViolation("SIGNAL_SNAPSHOT_INVALID")
        object.__setattr__(self, "values", MappingProxyType(values))
        for name in ("source_feature_manifest_ids", "history_feature_manifest_ids"):
            values = _identifiers(getattr(self, name), "SIGNAL_SNAPSHOT_LINEAGE_INVALID")
            if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in values):
                raise InvariantViolation("SIGNAL_SNAPSHOT_LINEAGE_INVALID")
            object.__setattr__(self, name, values)

    def payload(self) -> dict[str, object]:
        return {
            "signal_run_id": self.signal_run_id, "signal_id": self.signal_id,
            "signal_version": self.signal_version, "semantic_type": self.semantic_type,
            "as_of": self.as_of, "information_cutoff": self.information_cutoff,
            "values": dict(self.values), "quality_status": self.quality_status,
            "coverage": self.coverage,
            "source_feature_manifest_ids": list(self.source_feature_manifest_ids),
            "history_feature_manifest_ids": list(self.history_feature_manifest_ids),
            "universe_manifest_id": self.universe_manifest_id,
            "formula_version": self.formula_version,
            "parameter_set_id": self.parameter_set_id, "code_revision": self.code_revision,
            "validity": self.validity.payload(), "output_hash": self.output_hash,
        }
