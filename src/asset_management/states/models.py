"""Common contracts for the four independent state engines."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import re
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.horizon import SignalValidity
from asset_management.features.models import FeatureSnapshot
from asset_management.quality.models import BLOCKING_QUALITY, QualityStatus


STATE_SNAPSHOT_SCHEMA_VERSION = "state-snapshot-v2"
_HASH = re.compile(r"[0-9a-f]{64}")
_COMPONENT_ID = re.compile(r"[a-z][a-z0-9_.-]*")
_SEMANTIC = re.compile(r"[A-Z][A-Z0-9_]*")
_CODE_REVISION = re.compile(r"git:[0-9a-f]{7,40}")


class StateType(StrEnum):
    MARKET = "MARKET"
    COMPANY = "COMPANY"
    PORTFOLIO = "PORTFOLIO"
    SYSTEM = "SYSTEM"


class OperationalState(StrEnum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    REDUCED_RISK = "REDUCED_RISK"
    NO_NEW_TRADES = "NO_NEW_TRADES"
    HALTED = "HALTED"


class StateNormalization(StrEnum):
    """Meaning of a component value before any future state inference consumes it."""

    RAW = "RAW"
    Z_SCORE = "Z_SCORE"
    PERCENTILE = "PERCENTILE"
    DIRECTIONAL_SCORE = "DIRECTIONAL_SCORE"
    STANDARDIZED_COMPOSITE = "STANDARDIZED_COMPOSITE"
    STRUCTURED = "STRUCTURED"
    CATEGORICAL = "CATEGORICAL"


def _aware(value: str, reason: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(reason) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(reason)
    return parsed.astimezone(timezone.utc)


def _identifiers(values: tuple[str, ...], *, hashes: bool, reason: str,
                 allow_empty: bool = False) -> tuple[str, ...]:
    if ((not values and not allow_empty) or len(values) != len(set(values)) or
            any(not isinstance(value, str) or not value.strip() for value in values) or
            (hashes and any(not _HASH.fullmatch(value) for value in values))):
        raise ValueError(reason)
    return tuple(sorted(values))


def _feature_snapshot_payload(snapshot: FeatureSnapshot) -> dict[str, object]:
    return {
        "feature_run_id": snapshot.feature_run_id,
        "instrument_id": snapshot.instrument_id,
        "feature_id": snapshot.feature_id,
        "as_of": snapshot.as_of,
        "information_cutoff": snapshot.information_cutoff,
        "value": snapshot.value,
        "quality_status": snapshot.quality_status,
        "input_manifest_ids": list(snapshot.input_manifest_ids),
        "parameter_set_id": snapshot.parameter_set_id,
        "parent_state_id": snapshot.parent_state_id,
        "code_revision": snapshot.code_revision,
        "validity": snapshot.validity.payload(),
    }


@dataclass(frozen=True)
class StateFeatureInput:
    """One immutable FeatureSnapshot and the gold manifest that published it."""

    snapshot: FeatureSnapshot
    manifest_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, FeatureSnapshot) or not _HASH.fullmatch(self.manifest_id):
            raise ValueError("STATE_FEATURE_INPUT_INVALID")
        snapshot = self.snapshot
        if (not _HASH.fullmatch(snapshot.feature_run_id) or
                not isinstance(snapshot.instrument_id, str) or not snapshot.instrument_id.strip() or
                not isinstance(snapshot.feature_id, str) or not snapshot.feature_id.strip() or
                not isinstance(snapshot.parameter_set_id, str) or not snapshot.parameter_set_id.strip() or
                not _CODE_REVISION.fullmatch(snapshot.code_revision) or
                not isinstance(snapshot.validity, SignalValidity) or
                snapshot.quality_status not in {status.value for status in QualityStatus}):
            raise ValueError("STATE_FEATURE_SNAPSHOT_INVALID")
        as_of = _aware(snapshot.as_of, "STATE_FEATURE_TIME_INVALID")
        cutoff = _aware(snapshot.information_cutoff, "STATE_FEATURE_TIME_INVALID")
        if cutoff > as_of or snapshot.validity.valid_until <= as_of:
            raise ValueError("STATE_FEATURE_TIME_INVALID")
        _identifiers(tuple(snapshot.input_manifest_ids), hashes=True,
                     reason="STATE_FEATURE_SOURCE_LINEAGE_INVALID")

    def payload(self) -> dict[str, object]:
        return {"snapshot": _feature_snapshot_payload(self.snapshot), "manifest_id": self.manifest_id}


@dataclass(frozen=True)
class StateComponent:
    value: object
    component_id: str
    semantic_type: str
    unit: str
    normalization: StateNormalization
    as_of: str
    information_cutoff: str
    confidence: Decimal
    quality_status: QualityStatus
    freshness_seconds: int
    input_evidence_ids: tuple[str, ...]
    parameter_set_id: str
    formula_version: str
    input_features: tuple[StateFeatureInput, ...] = ()
    calculation_lineage_id: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.component_id, str) or not _COMPONENT_ID.fullmatch(self.component_id) or
                not isinstance(self.semantic_type, str) or not _SEMANTIC.fullmatch(self.semantic_type) or
                not isinstance(self.unit, str) or not self.unit.strip() or
                not isinstance(self.normalization, StateNormalization) or
                not isinstance(self.quality_status, QualityStatus) or
                not isinstance(self.parameter_set_id, str) or not self.parameter_set_id.strip() or
                not isinstance(self.formula_version, str) or not self.formula_version.strip()):
            raise ValueError("STATE_COMPONENT_SEMANTICS_INVALID")
        as_of = _aware(self.as_of, "STATE_COMPONENT_TIME_INVALID")
        cutoff = _aware(self.information_cutoff, "STATE_COMPONENT_TIME_INVALID")
        if cutoff > as_of:
            raise ValueError("STATE_COMPONENT_CUTOFF_AFTER_AS_OF")
        if (not isinstance(self.confidence, Decimal) or not self.confidence.is_finite() or
                not Decimal(0) <= self.confidence <= Decimal(1)):
            raise ValueError("STATE_CONFIDENCE_INVALID")
        if type(self.freshness_seconds) is not int or self.freshness_seconds < 0:
            raise ValueError("STATE_FRESHNESS_INVALID")
        object.__setattr__(self, "input_evidence_ids", _identifiers(
            self.input_evidence_ids, hashes=True, reason="STATE_EVIDENCE_LINEAGE_INVALID"))
        if (len(self.input_features) != len({(item.snapshot.feature_run_id, item.manifest_id)
                                             for item in self.input_features}) or
                any(not isinstance(item, StateFeatureInput) for item in self.input_features)):
            raise ValueError("STATE_FEATURE_INPUTS_INVALID")
        ordered = tuple(sorted(
            self.input_features,
            key=lambda item: (item.snapshot.instrument_id, item.snapshot.feature_id,
                              item.snapshot.feature_run_id, item.manifest_id),
        ))
        for item in ordered:
            feature_as_of = _aware(item.snapshot.as_of, "STATE_FEATURE_TIME_INVALID")
            feature_cutoff = _aware(item.snapshot.information_cutoff, "STATE_FEATURE_TIME_INVALID")
            if (feature_as_of > as_of or feature_cutoff > cutoff or
                    item.snapshot.validity.valid_until <= as_of):
                raise ValueError("STATE_FEATURE_CONTEXT_INVALID")
        object.__setattr__(self, "input_features", ordered)
        if self.calculation_lineage_id is not None and not _HASH.fullmatch(self.calculation_lineage_id):
            raise ValueError("STATE_CALCULATION_LINEAGE_INVALID")
        object.__setattr__(self, "as_of", as_of.isoformat())
        object.__setattr__(self, "information_cutoff", cutoff.isoformat())

    def payload(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "value": _jsonable(self.value),
            "semantic_type": self.semantic_type,
            "unit": self.unit,
            "normalization": self.normalization.value,
            "as_of": self.as_of,
            "information_cutoff": self.information_cutoff,
            "confidence": str(self.confidence),
            "quality_status": self.quality_status.value,
            "freshness_seconds": self.freshness_seconds,
            "input_evidence_ids": list(self.input_evidence_ids),
            "parameter_set_id": self.parameter_set_id,
            "formula_version": self.formula_version,
            "input_features": [item.payload() for item in self.input_features],
            "calculation_lineage_id": self.calculation_lineage_id,
        }


@dataclass(frozen=True)
class StatePolicy:
    policy_version: str
    caution_confidence: Decimal
    minimum_confidence: Decimal
    stale_after_seconds: int
    caution_risk_multiplier: Decimal = Decimal("0.75")
    reduced_risk_multiplier: Decimal = Decimal("0.50")

    def __post_init__(self) -> None:
        decimals = (self.minimum_confidence, self.caution_confidence,
                    self.reduced_risk_multiplier, self.caution_risk_multiplier)
        if (not self.policy_version.strip() or
                any(not isinstance(value, Decimal) or not value.is_finite() for value in decimals) or
                not Decimal(0) <= self.minimum_confidence <= self.caution_confidence <= Decimal(1) or
                type(self.stale_after_seconds) is not int or self.stale_after_seconds < 0 or
                not Decimal(0) <= self.reduced_risk_multiplier <= self.caution_risk_multiplier <= Decimal(1)):
            raise ValueError("STATE_POLICY_INVALID")


@dataclass(frozen=True)
class StateSnapshot:
    state_id: str
    state_type: StateType
    as_of: str
    information_cutoff: str
    components: Mapping[str, StateComponent]
    confidence: str
    quality_status: QualityStatus
    freshness: int
    input_evidence_ids: tuple[str, ...]
    input_feature_ids: tuple[str, ...]
    input_feature_run_ids: tuple[str, ...]
    input_feature_manifest_ids: tuple[str, ...]
    input_data_manifest_ids: tuple[str, ...]
    calculation_lineage_ids: tuple[str, ...]
    policy_version: str
    code_revision: str
    operational_state: OperationalState
    risk_multiplier: str
    regime_label: str | None = None
    schema_version: str = STATE_SNAPSHOT_SCHEMA_VERSION

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "state_id": self.state_id,
            "state_type": self.state_type.value,
            "as_of": self.as_of,
            "information_cutoff": self.information_cutoff,
            "components": {name: component.payload()
                           for name, component in sorted(self.components.items())},
            "confidence": self.confidence,
            "quality_status": self.quality_status.value,
            "freshness": self.freshness,
            "input_evidence_ids": list(self.input_evidence_ids),
            "input_feature_ids": list(self.input_feature_ids),
            "input_feature_run_ids": list(self.input_feature_run_ids),
            "input_feature_manifest_ids": list(self.input_feature_manifest_ids),
            "input_data_manifest_ids": list(self.input_data_manifest_ids),
            "calculation_lineage_ids": list(self.calculation_lineage_ids),
            "policy_version": self.policy_version,
            "code_revision": self.code_revision,
            "operational_state": self.operational_state.value,
            "risk_multiplier": self.risk_multiplier,
            "regime_label": self.regime_label,
        }


def state_identity(*, state_type: StateType, as_of: datetime, information_cutoff: datetime,
                   components: Mapping[str, StateComponent], policy: StatePolicy,
                   code_revision: str, regime_label: str | None) -> str:
    body = {
        "schema_version": STATE_SNAPSHOT_SCHEMA_VERSION,
        "state_type": state_type.value,
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "information_cutoff": information_cutoff.astimezone(timezone.utc).isoformat(),
        "components": {name: component.payload() for name, component in sorted(components.items())},
        "policy": {**asdict(policy), "caution_confidence": str(policy.caution_confidence),
                   "minimum_confidence": str(policy.minimum_confidence),
                   "caution_risk_multiplier": str(policy.caution_risk_multiplier),
                   "reduced_risk_multiplier": str(policy.reduced_risk_multiplier)},
        "code_revision": code_revision,
        "regime_label": regime_label,
    }
    return digest(canonical(body))


def worst_quality(components: Mapping[str, StateComponent]) -> QualityStatus:
    order = (QualityStatus.QUARANTINED, QualityStatus.BLOCKED, QualityStatus.CONFLICT,
             QualityStatus.MISSING, QualityStatus.STALE, QualityStatus.PRIMARY_PENDING,
             QualityStatus.VENDOR_DELAY, QualityStatus.ESTIMATED, QualityStatus.MANUAL,
             QualityStatus.VALID)
    return next(status for status in order
                if any(component.quality_status is status for component in components.values()))


def is_blocking(status: QualityStatus) -> bool:
    return status in BLOCKING_QUALITY


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("STATE_VALUE_NOT_FINITE")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("STATE_VALUE_TIME_NOT_AWARE")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
