"""Propagate data quality to features, state, confidence, and risk decisions."""

from __future__ import annotations

from typing import Iterable

from .models import BLOCKING_QUALITY, QualityGate, QualityReport, QualityStatus, SourceHealthSnapshot, SourceHealthStatus


def propagate_quality(reports: Iterable[QualityReport], source_health: Iterable[SourceHealthSnapshot]) -> QualityGate:
    reports = tuple(reports)
    health = tuple(source_health)
    reasons = [issue.code for report in reports for issue in report.issues]
    reasons.extend(f"SOURCE_{item.source}_{item.status}" for item in health
                   if item.status is not SourceHealthStatus.NORMAL)
    severe = next((report.status for report in reports if report.status in BLOCKING_QUALITY), None)
    unhealthy = any(item.status in {SourceHealthStatus.STALE, SourceHealthStatus.BLOCKED,
                                   SourceHealthStatus.UNKNOWN} for item in health)
    if severe is not None or unhealthy:
        status = severe or QualityStatus.BLOCKED
        return QualityGate("NO_TRADE", tuple(dict.fromkeys(reasons)) or ("DATA_QUALITY_BLOCKED",),
                           status, status, "BLOCKED")
    non_valid = next((report.status for report in reports if report.status is not QualityStatus.VALID), None)
    if non_valid is not None or any(item.status is SourceHealthStatus.DEGRADED for item in health):
        status = non_valid or QualityStatus.ESTIMATED
        return QualityGate("REDUCE", tuple(dict.fromkeys(reasons)) or ("QUALITY_DEGRADED",),
                           status, status, "LOW")
    return QualityGate("ALLOW", (), QualityStatus.VALID, QualityStatus.VALID, "HIGH")
