from datetime import datetime, timedelta, timezone
from decimal import Decimal

from asset_management.quality.issue_registry import QualityIssueRegistry
from asset_management.quality.models import QualityIssue, QualityReport, QualityStatus, SourceHealthStatus
from asset_management.quality.quality_propagation import propagate_quality
from asset_management.quality.source_health import assess_source_health
from asset_management.quality.validators import (
    validate_completeness, validate_cross_source, validate_market_ranges,
    validate_schema, validate_times,
)


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


def good_row():
    return {
        "instrument_id": "SPY", "event_time_utc": "2026-09-04T20:00:00+00:00",
        "available_at": "2026-09-04T20:01:00+00:00", "exchange": "XNYS",
        "exchange_local_date": "2026-09-04", "open": "640", "high": "645",
        "low": "638", "close": "643", "volume": 100,
    }


def test_valid_data_and_normal_source_can_reach_decision():
    row = good_row()
    schema = {"instrument_id": "string", "event_time_utc": "timestamp",
              "available_at": "timestamp", "close": "decimal", "volume": "integer"}
    reports = [
        validate_schema([row], schema),
        validate_market_ranges([row], key_fields=("instrument_id", "event_time_utc")),
        validate_times([row], observed_at=NOW,
                       session_dates={"XNYS": {"2026-09-04"}}),
        validate_completeness([row], required_entities={"SPY"}, expected_dates={"2026-09-04"}),
    ]
    assert all(item.valid for item in reports)
    health = assess_source_health(source="tiingo", observed_at=NOW,
                                  last_success_at=NOW - timedelta(minutes=1), last_failure_at=None,
                                  error_count=0, schema_status=QualityStatus.VALID,
                                  stale_after_seconds=300)
    assert health.status is SourceHealthStatus.NORMAL
    gate = propagate_quality(reports, [health])
    assert gate.action == "ALLOW" and gate.decision_confidence == "HIGH"


def test_schema_timezone_and_missing_are_not_filled_with_zero():
    row = {"instrument_id": "SPY", "event_time_utc": "2026-09-04T20:00:00", "close": None}
    report = validate_schema([row], {"instrument_id": "string", "event_time_utc": "timestamp",
                                         "close": "decimal"})
    assert report.status is QualityStatus.QUARANTINED
    assert {issue.code for issue in report.issues} == {"TIMEZONE_MISSING", "REQUIRED_FIELD_MISSING"}
    assert row["close"] is None


def test_range_duplicate_and_time_failures_are_explicit():
    row = good_row() | {"open": "650", "high": "640", "low": "645", "close": "-1",
                        "volume": -2, "available_at": "2026-09-04T19:00:00+00:00"}
    ranges = validate_market_ranges([row, row], key_fields=("instrument_id", "event_time_utc"))
    assert ranges.status is QualityStatus.QUARANTINED
    codes = {issue.code for issue in ranges.issues}
    assert {"NEGATIVE_PRICE", "NEGATIVE_QUANTITY", "OHLC_CONFLICT", "DUPLICATE_ROW"} <= codes
    times = validate_times([row], observed_at=NOW, session_dates={"XNYS": set()})
    assert {issue.code for issue in times.issues} == {"AVAILABLE_BEFORE_EVENT", "SESSION_MISMATCH"}


def test_future_and_reverse_chronology_are_blocked():
    future = good_row() | {"event_time_utc": "2026-09-06T20:00:00+00:00",
                           "available_at": "2026-09-06T20:01:00+00:00"}
    older = good_row() | {"event_time_utc": "2026-09-03T20:00:00+00:00",
                          "available_at": "2026-09-03T20:01:00+00:00"}
    report = validate_times([future, older], observed_at=NOW)
    assert {issue.code for issue in report.issues} == {"FUTURE_TIMESTAMP", "REVERSE_CHRONOLOGY"}


def test_time_and_range_checks_quarantine_malformed_values():
    row = good_row() | {"event_time_utc": "2026-09-04T20:00:00", "open": "bad"}
    assert validate_times([row], observed_at=NOW).status is QualityStatus.QUARANTINED
    report = validate_market_ranges([row], key_fields=("instrument_id", "event_time_utc"))
    assert report.status is QualityStatus.QUARANTINED
    assert "FIELD_TYPE_INVALID" in {issue.code for issue in report.issues}


def test_unrealistic_interest_rate_uses_explicit_policy_bounds():
    row = good_row() | {"yield": "99"}
    report = validate_market_ranges([row], key_fields=("instrument_id", "event_time_utc"),
                                    rate_bounds={"yield": (Decimal("-5"), Decimal("30"))})
    assert report.status is QualityStatus.QUARANTINED
    assert report.issues[0].code == "RATE_OUT_OF_POLICY_RANGE"
    assert report.issues[0].details == {"lower": "-5", "upper": "30"}


def test_completeness_detects_trading_days_universe_and_actions():
    report = validate_completeness([good_row()], required_entities={"SPY", "QQQ"},
                                   expected_dates={"2026-09-03", "2026-09-04"},
                                   corporate_action_entities={"SPY"}, adjusted_entities=set())
    codes = {issue.code for issue in report.issues}
    assert {"UNIVERSE_ENTITY_MISSING", "TRADING_DAY_MISSING", "CORPORATE_ACTION_NOT_REFLECTED"} <= codes
    assert report.status is QualityStatus.BLOCKED


def test_cross_source_conflict_is_recorded_and_never_averaged(tmp_path):
    left = [{"instrument_id": "SPY", "date": "2026-09-04", "close": "100"}]
    right = [{"instrument_id": "SPY", "date": "2026-09-04", "close": "105"}]
    report = validate_cross_source(left, right, key_fields=("instrument_id", "date"),
                                   value_field="close", absolute_tolerance=Decimal("1"))
    assert report.status is QualityStatus.CONFLICT
    assert report.issues[0].details["absolute_difference"] == "5"
    assert left[0]["close"] == "100" and right[0]["close"] == "105"
    registry = QualityIssueRegistry(tmp_path / "issues.jsonl")
    first = registry.record(dataset="daily-prices", manifest_id="m1", issue=report.issues[0], recorded_at=NOW)
    second = registry.record(dataset="daily-prices", manifest_id="m1", issue=report.issues[0], recorded_at=NOW)
    assert first == second and len(registry.read_all()) == 1


def test_stale_source_blocks_features_and_decision():
    stale = assess_source_health(source="fred", observed_at=NOW,
                                 last_success_at=NOW - timedelta(hours=2), last_failure_at=None,
                                 error_count=0, schema_status=QualityStatus.VALID,
                                 stale_after_seconds=300)
    assert stale.status is SourceHealthStatus.STALE
    gate = propagate_quality([QualityReport(QualityStatus.VALID)], [stale])
    assert gate.action == "NO_TRADE"
    assert gate.feature_quality is QualityStatus.BLOCKED
    assert "SOURCE_fred_STALE" in gate.reason_codes


def test_missing_conflict_and_quarantine_propagate_to_no_trade():
    for status in (QualityStatus.MISSING, QualityStatus.CONFLICT, QualityStatus.QUARANTINED):
        report = QualityReport(status, (QualityIssue(f"{status}_INPUT", status, "bad input"),))
        gate = propagate_quality([report], [])
        assert gate.action == "NO_TRADE" and gate.state_quality is status


def test_degraded_source_reduces_confidence_but_unknown_blocks():
    degraded = assess_source_health(source="tiingo", observed_at=NOW,
                                    last_success_at=NOW - timedelta(seconds=10),
                                    last_failure_at=NOW - timedelta(seconds=5), error_count=1,
                                    schema_status=QualityStatus.VALID, stale_after_seconds=60)
    assert propagate_quality([QualityReport(QualityStatus.VALID)], [degraded]).action == "REDUCE"
    unknown = assess_source_health(source="new", observed_at=NOW, last_success_at=None,
                                   last_failure_at=None, error_count=0,
                                   schema_status=QualityStatus.VALID, stale_after_seconds=60)
    assert propagate_quality([QualityReport(QualityStatus.VALID)], [unknown]).action == "NO_TRADE"
