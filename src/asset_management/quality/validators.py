"""Schema, range, time, completeness, and cross-source validators."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Sequence

from .models import QualityIssue, QualityReport, QualityStatus


def _issue(code: str, status: QualityStatus, message: str, *, field: str | None = None,
           row_key: str | None = None, details: Mapping[str, object] | None = None) -> QualityIssue:
    return QualityIssue(code, status, message, field, row_key, details or {})


def _report(issues: list[QualityIssue]) -> QualityReport:
    priority = (QualityStatus.QUARANTINED, QualityStatus.BLOCKED, QualityStatus.CONFLICT,
                QualityStatus.MISSING, QualityStatus.STALE)
    status = next((item for item in priority if any(i.status is item for i in issues)),
                  QualityStatus.VALID)
    return QualityReport(status, tuple(issues))


def validate_schema(rows: Sequence[Mapping[str, object]], schema: Mapping[str, str],
                    *, required_fields: Iterable[str] | None = None) -> QualityReport:
    """Validate required fields and the types used by immutable silver datasets."""
    required = tuple(required_fields or schema)
    issues: list[QualityIssue] = []
    for index, row in enumerate(rows):
        key = str(row.get("instrument_id", row.get("entity_id", index)))
        for name in required:
            if name not in row or row[name] is None or row[name] == "":
                issues.append(_issue("REQUIRED_FIELD_MISSING", QualityStatus.MISSING,
                                     f"required field {name} is missing", field=name, row_key=key))
        for name, kind in schema.items():
            if name not in row or row[name] is None:
                continue
            value = row[name]
            try:
                if kind == "decimal":
                    number = Decimal(str(value))
                    if not number.is_finite():
                        raise ValueError
                elif kind == "integer" and (isinstance(value, bool) or int(value) != value):
                    raise ValueError
                elif kind == "boolean" and not isinstance(value, bool):
                    raise ValueError
                elif kind == "string" and not isinstance(value, str):
                    raise ValueError
                elif kind == "timestamp":
                    instant = datetime.fromisoformat(str(value))
                    if instant.tzinfo is None or instant.utcoffset() is None:
                        issues.append(_issue("TIMEZONE_MISSING", QualityStatus.QUARANTINED,
                                             f"{name} must include a timezone", field=name, row_key=key))
            except (ValueError, TypeError, InvalidOperation):
                issues.append(_issue("FIELD_TYPE_INVALID", QualityStatus.QUARANTINED,
                                     f"{name} does not match {kind}", field=name, row_key=key))
    return _report(issues)


def validate_market_ranges(rows: Sequence[Mapping[str, object]], *, key_fields: Sequence[str],
                           rate_bounds: Mapping[str, tuple[Decimal, Decimal]] | None = None) -> QualityReport:
    issues: list[QualityIssue] = []
    keys: list[tuple[object, ...]] = []
    for index, row in enumerate(rows):
        key = tuple(row.get(field) for field in key_fields)
        keys.append(key)
        label = "|".join(map(str, key)) if key else str(index)
        decimals: dict[str, Decimal] = {}
        for name in ("open", "high", "low", "close", "price"):
            if row.get(name) is not None:
                try:
                    decimals[name] = Decimal(str(row[name]))
                    if decimals[name] < 0:
                        issues.append(_issue("NEGATIVE_PRICE", QualityStatus.QUARANTINED,
                                             f"{name} cannot be negative", field=name, row_key=label))
                except (InvalidOperation, ValueError, TypeError):
                    issues.append(_issue("FIELD_TYPE_INVALID", QualityStatus.QUARANTINED,
                                         f"{name} is not a decimal", field=name, row_key=label))
        if {"high", "low"} <= decimals.keys() and decimals["high"] < decimals["low"]:
            issues.append(_issue("OHLC_CONFLICT", QualityStatus.CONFLICT,
                                 "high is below low", row_key=label))
        if {"high", "low", "open"} <= decimals.keys() and not decimals["low"] <= decimals["open"] <= decimals["high"]:
            issues.append(_issue("OHLC_CONFLICT", QualityStatus.CONFLICT,
                                 "open is outside the low/high range", row_key=label))
        if {"high", "low", "close"} <= decimals.keys() and not decimals["low"] <= decimals["close"] <= decimals["high"]:
            issues.append(_issue("OHLC_CONFLICT", QualityStatus.CONFLICT,
                                 "close is outside the low/high range", row_key=label))
        for name in ("volume", "analyst_count", "diluted_shares"):
            if row.get(name) is not None:
                try:
                    if Decimal(str(row[name])) < 0:
                        issues.append(_issue("NEGATIVE_QUANTITY", QualityStatus.QUARANTINED,
                                             f"{name} cannot be negative", field=name, row_key=label))
                except (InvalidOperation, ValueError, TypeError):
                    issues.append(_issue("FIELD_TYPE_INVALID", QualityStatus.QUARANTINED,
                                         f"{name} is not numeric", field=name, row_key=label))
        for name, bounds in (rate_bounds or {}).items():
            if row.get(name) is None:
                continue
            try:
                rate = Decimal(str(row[name]))
                lower, upper = bounds
                if not rate.is_finite() or rate < lower or rate > upper:
                    issues.append(_issue("RATE_OUT_OF_POLICY_RANGE", QualityStatus.QUARANTINED,
                                         f"{name} is outside the approved range", field=name,
                                         row_key=label, details={"lower": str(lower), "upper": str(upper)}))
            except (InvalidOperation, ValueError, TypeError):
                issues.append(_issue("FIELD_TYPE_INVALID", QualityStatus.QUARANTINED,
                                     f"{name} is not a decimal", field=name, row_key=label))
    for key, count in Counter(keys).items():
        if count > 1:
            issues.append(_issue("DUPLICATE_ROW", QualityStatus.CONFLICT,
                                 "duplicate logical row", row_key="|".join(map(str, key))))
    return _report(issues)


def validate_times(rows: Sequence[Mapping[str, object]], *, observed_at: datetime,
                   event_field: str = "event_time_utc", available_field: str = "available_at",
                   session_dates: Mapping[str, set[str]] | None = None) -> QualityReport:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("OBSERVED_AT_NOT_AWARE")
    now = observed_at.astimezone(timezone.utc)
    issues: list[QualityIssue] = []
    previous: dict[str, datetime] = {}
    for index, row in enumerate(rows):
        label = str(row.get("instrument_id", row.get("entity_id", index)))
        try:
            event = datetime.fromisoformat(str(row[event_field]))
            available = datetime.fromisoformat(str(row[available_field]))
        except (KeyError, ValueError, TypeError):
            continue
        if (event.tzinfo is None or event.utcoffset() is None or
                available.tzinfo is None or available.utcoffset() is None):
            issues.append(_issue("TIMEZONE_MISSING", QualityStatus.QUARANTINED,
                                 "event and availability times require timezone", row_key=label))
            continue
        event = event.astimezone(timezone.utc)
        available = available.astimezone(timezone.utc)
        if available < event:
            issues.append(_issue("AVAILABLE_BEFORE_EVENT", QualityStatus.QUARANTINED,
                                 "available_at precedes event time", row_key=label))
        if event > now or available > now:
            issues.append(_issue("FUTURE_TIMESTAMP", QualityStatus.QUARANTINED,
                                 "timestamp is later than observation time", row_key=label))
        if label in previous and event < previous[label]:
            issues.append(_issue("REVERSE_CHRONOLOGY", QualityStatus.CONFLICT,
                                 "events are not chronological", row_key=label))
        previous[label] = event
        if session_dates is not None:
            exchange = str(row.get("exchange", ""))
            local_date = str(row.get("exchange_local_date", ""))
            if local_date not in session_dates.get(exchange, set()):
                issues.append(_issue("SESSION_MISMATCH", QualityStatus.BLOCKED,
                                     "row does not belong to an open exchange session", row_key=label))
    return _report(issues)


def validate_completeness(rows: Sequence[Mapping[str, object]], *, required_entities: Iterable[str],
                          entity_field: str = "instrument_id",
                          expected_dates: Iterable[str] | None = None,
                          date_field: str = "exchange_local_date",
                          corporate_action_entities: Iterable[str] = (),
                          adjusted_entities: Iterable[str] = ()) -> QualityReport:
    issues: list[QualityIssue] = []
    required = set(required_entities)
    present = {str(row[entity_field]) for row in rows if row.get(entity_field) is not None}
    for entity in sorted(required - present):
        issues.append(_issue("UNIVERSE_ENTITY_MISSING", QualityStatus.MISSING,
                             "required universe entity has no data", row_key=entity))
    if expected_dates is not None:
        actual = {(str(row.get(entity_field)), str(row.get(date_field))) for row in rows}
        for entity in sorted(required):
            for date in expected_dates:
                if (entity, str(date)) not in actual:
                    issues.append(_issue("TRADING_DAY_MISSING", QualityStatus.MISSING,
                                         "expected trading-day observation is missing",
                                         row_key=f"{entity}|{date}"))
    for entity in sorted(set(corporate_action_entities) - set(adjusted_entities)):
        issues.append(_issue("CORPORATE_ACTION_NOT_REFLECTED", QualityStatus.BLOCKED,
                             "corporate action is not reflected in the series", row_key=entity))
    return _report(issues)


def validate_cross_source(left: Sequence[Mapping[str, object]], right: Sequence[Mapping[str, object]],
                          *, key_fields: Sequence[str], value_field: str,
                          absolute_tolerance: Decimal) -> QualityReport:
    """Record material disagreement; conflicting values are never averaged."""
    def indexed(rows: Sequence[Mapping[str, object]]) -> dict[tuple[object, ...], Decimal]:
        return {tuple(row.get(k) for k in key_fields): Decimal(str(row[value_field]))
                for row in rows if row.get(value_field) is not None}
    a, b = indexed(left), indexed(right)
    issues: list[QualityIssue] = []
    for key in sorted(a.keys() & b.keys(), key=str):
        difference = abs(a[key] - b[key])
        if difference > absolute_tolerance:
            issues.append(_issue("CROSS_SOURCE_CONFLICT", QualityStatus.CONFLICT,
                                 "provider values exceed tolerance", row_key="|".join(map(str, key)),
                                 details={"absolute_difference": str(difference),
                                          "tolerance": str(absolute_tolerance)}))
    return _report(issues)
