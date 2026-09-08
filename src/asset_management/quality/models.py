"""Phase 10 data-quality and provider-health contracts."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping


class QualityStatus(StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    PRIMARY_PENDING = "PRIMARY_PENDING"
    VENDOR_DELAY = "VENDOR_DELAY"
    ESTIMATED = "ESTIMATED"
    MANUAL = "MANUAL"
    BLOCKED = "BLOCKED"
    QUARANTINED = "QUARANTINED"


class SourceHealthStatus(StrEnum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


BLOCKING_QUALITY = frozenset({
    QualityStatus.STALE, QualityStatus.MISSING, QualityStatus.CONFLICT,
    QualityStatus.PRIMARY_PENDING, QualityStatus.BLOCKED, QualityStatus.QUARANTINED,
})


@dataclass(frozen=True)
class QualityIssue:
    code: str
    status: QualityStatus
    message: str
    field: str | None = None
    row_key: str | None = None
    details: Mapping[str, Any] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class QualityReport:
    status: QualityStatus
    issues: tuple[QualityIssue, ...] = ()
    checked_at: datetime = dataclass_field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def valid(self) -> bool:
        return self.status is QualityStatus.VALID and not self.issues

    @property
    def decision_eligible(self) -> bool:
        return self.valid


@dataclass(frozen=True)
class SourceHealthSnapshot:
    source: str
    status: SourceHealthStatus
    last_success_at: datetime | None
    last_failure_at: datetime | None
    lag_seconds: int | None
    error_count: int
    schema_status: QualityStatus
    fallback_status: str
    action_required: str | None
    observed_at: datetime

    def __post_init__(self) -> None:
        for value in (self.last_success_at, self.last_failure_at, self.observed_at):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("SOURCE_HEALTH_TIME_NOT_AWARE")
        if self.lag_seconds is not None and self.lag_seconds < 0:
            raise ValueError("SOURCE_HEALTH_LAG_NEGATIVE")
        if self.error_count < 0:
            raise ValueError("SOURCE_HEALTH_ERROR_COUNT_NEGATIVE")


@dataclass(frozen=True)
class QualityGate:
    action: str
    reason_codes: tuple[str, ...]
    feature_quality: QualityStatus
    state_quality: QualityStatus
    decision_confidence: str

    @property
    def can_trade(self) -> bool:
        return self.action != "NO_TRADE"
