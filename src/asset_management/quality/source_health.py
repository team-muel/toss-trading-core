"""Provider health derived from immutable observations."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import QualityStatus, SourceHealthSnapshot, SourceHealthStatus


def assess_source_health(*, source: str, observed_at: datetime,
                         last_success_at: datetime | None, last_failure_at: datetime | None,
                         error_count: int, schema_status: QualityStatus,
                         stale_after_seconds: int, fallback_status: str = "UNAVAILABLE") -> SourceHealthSnapshot:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("SOURCE_HEALTH_TIME_NOT_AWARE")
    if any(value is not None and (value.tzinfo is None or value.utcoffset() is None)
           for value in (last_success_at, last_failure_at)):
        raise ValueError("SOURCE_HEALTH_TIME_NOT_AWARE")
    if stale_after_seconds < 0:
        raise ValueError("SOURCE_HEALTH_THRESHOLD_NEGATIVE")
    now = observed_at.astimezone(timezone.utc)
    success = last_success_at.astimezone(timezone.utc) if last_success_at else None
    lag = int((now - success).total_seconds()) if success else None
    if schema_status in {QualityStatus.BLOCKED, QualityStatus.QUARANTINED,
                         QualityStatus.CONFLICT, QualityStatus.MISSING}:
        status, action = SourceHealthStatus.BLOCKED, "repair schema or quarantined dataset"
    elif success is None:
        status, action = SourceHealthStatus.UNKNOWN, "establish first successful observation"
    elif lag is not None and lag > stale_after_seconds:
        status, action = SourceHealthStatus.STALE, "restore collection before feature generation"
    elif error_count > 0 or (last_failure_at and last_failure_at >= last_success_at):
        status, action = SourceHealthStatus.DEGRADED, "investigate provider failures"
    else:
        status, action = SourceHealthStatus.NORMAL, None
    return SourceHealthSnapshot(source, status, success,
                                last_failure_at.astimezone(timezone.utc) if last_failure_at else None,
                                lag, error_count, schema_status, fallback_status, action, now)
