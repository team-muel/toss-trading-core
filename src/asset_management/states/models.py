"""Common contracts for the four independent state engines."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.quality.models import BLOCKING_QUALITY, QualityStatus


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


@dataclass(frozen=True)
class StateComponent:
    value: object
    confidence: Decimal
    quality_status: QualityStatus
    freshness_seconds: int
    input_feature_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not Decimal(0) <= self.confidence <= Decimal(1):
            raise ValueError("STATE_CONFIDENCE_INVALID")
        if self.freshness_seconds < 0:
            raise ValueError("STATE_FRESHNESS_INVALID")
        if (not self.input_feature_ids or len(self.input_feature_ids) != len(set(self.input_feature_ids))
                or any(not item.strip() for item in self.input_feature_ids)):
            raise ValueError("STATE_FEATURE_LINEAGE_INVALID")


@dataclass(frozen=True)
class StatePolicy:
    policy_version: str
    caution_confidence: Decimal
    minimum_confidence: Decimal
    stale_after_seconds: int
    caution_risk_multiplier: Decimal = Decimal("0.75")
    reduced_risk_multiplier: Decimal = Decimal("0.50")

    def __post_init__(self) -> None:
        if (not self.policy_version.strip() or
                not Decimal(0) <= self.minimum_confidence <= self.caution_confidence <= Decimal(1) or
                self.stale_after_seconds < 0 or
                not Decimal(0) <= self.reduced_risk_multiplier <= self.caution_risk_multiplier <= Decimal(1)):
            raise ValueError("STATE_POLICY_INVALID")


@dataclass(frozen=True)
class StateSnapshot:
    state_id: str
    state_type: StateType
    as_of: str
    components: Mapping[str, StateComponent]
    confidence: str
    quality_status: QualityStatus
    freshness: int
    input_feature_ids: tuple[str, ...]
    policy_version: str
    code_revision: str
    operational_state: OperationalState
    risk_multiplier: str
    regime_label: str | None = None

    def payload(self) -> dict:
        result = asdict(self)
        result["state_type"] = str(self.state_type)
        result["quality_status"] = str(self.quality_status)
        result["operational_state"] = str(self.operational_state)
        result["components"] = {
            name: {**asdict(component), "value": _jsonable(component.value),
                   "confidence": str(component.confidence),
                   "quality_status": str(component.quality_status),
                   "input_feature_ids": list(component.input_feature_ids)}
            for name, component in sorted(self.components.items())
        }
        result["input_feature_ids"] = list(self.input_feature_ids)
        return result


def state_identity(*, state_type: StateType, as_of: datetime,
                   components: Mapping[str, StateComponent], policy: StatePolicy,
                   code_revision: str, regime_label: str | None) -> str:
    body = {
        "state_type": str(state_type), "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "components": {
            name: {"value": _jsonable(component.value), "confidence": str(component.confidence),
                   "quality_status": str(component.quality_status),
                   "freshness_seconds": component.freshness_seconds,
                   "input_feature_ids": sorted(component.input_feature_ids)}
            for name, component in sorted(components.items())
        },
        "policy": {**asdict(policy), "caution_confidence": str(policy.caution_confidence),
                   "minimum_confidence": str(policy.minimum_confidence),
                   "caution_risk_multiplier": str(policy.caution_risk_multiplier),
                   "reduced_risk_multiplier": str(policy.reduced_risk_multiplier)},
        "code_revision": code_revision, "regime_label": regime_label,
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
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
