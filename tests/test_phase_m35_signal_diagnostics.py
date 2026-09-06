from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.quality.models import QualityStatus
from asset_management.signals import (
    CrossSectionalObservation, DiagnosticConfig, SignalDiagnosticsStore, SignalSnapshot,
    TimeSeriesObservation,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
UNIVERSE = ("AAA", "BBB", "CCC", "DDD", "EEE")
UNIVERSE_ID = "a" * 64


def cross_observation(*, number=1, horizon=5, values=None, available_at=None):
    as_of = NOW - timedelta(days=20 + number)
    values = values or {
        "AAA": Decimal("0.1"), "BBB": Decimal("0.2"), "CCC": Decimal("0.3"),
        "DDD": Decimal("0.4"), "EEE": Decimal("0.5"),
    }
    rendered_values = {key: None if value is None else str(value) for key, value in values.items()}
    snapshot = SignalSnapshot(
        f"{number:064x}", "value.relative_strength", "1", "SIGNAL_VALUE",
        as_of.isoformat(), as_of.isoformat(), rendered_values, QualityStatus.VALID.value,
        str(Decimal(sum(value is not None for value in values.values())) / Decimal(len(values))),
        ("b" * 64,), ("c" * 64,), UNIVERSE_ID, "relative-strength@1",
        "signal-parameters-v1", "git:abcdef0",
        SignalValidity(21, 21, NOW + timedelta(days=1), DecayProfile.LINEAR),
        digest(canonical(rendered_values)),
    )
    return CrossSectionalObservation(
        signal_run_id=f"{number:064x}", signal_id="value.relative_strength", signal_version="1",
        as_of=as_of, information_cutoff=as_of, embargo_until=as_of + timedelta(days=5),
        outcome_available_at=available_at or as_of + timedelta(days=5),
        universe_manifest_id=UNIVERSE_ID, universe=UNIVERSE,
        signal_values=values,
        forward_returns={
            "AAA": Decimal("0.01"), "BBB": Decimal("0.02"), "CCC": Decimal("0.03"),
            "DDD": Decimal("0.04"), "EEE": Decimal("0.05"),
        },
        bucket_labels={
            "AAA": {"sector": "energy", "size": "large", "liquidity": "high"},
            "BBB": {"sector": "energy", "size": "large", "liquidity": "high"},
            "CCC": {"sector": "technology", "size": "mid", "liquidity": "medium"},
            "DDD": {"sector": "technology", "size": "mid", "liquidity": "medium"},
            "EEE": {"sector": "health", "size": "small", "liquidity": "low"},
        },
        previous_top_quantile_members=("AAA",), holding_period_overlap=Decimal("0.6"),
        horizon_days=horizon, code_revision="git:abcdef0", signal_snapshot=snapshot,
    )


def time_observation(number, forecast, realized, *, available_at=None):
    as_of = NOW - timedelta(days=20 + number)
    snapshot_values = {"ETF": forecast}
    snapshot = SignalSnapshot(
        f"{100 + number:064x}", "trend.etf", "2", "SIGNAL_VALUE", as_of.isoformat(),
        as_of.isoformat(), snapshot_values, QualityStatus.VALID.value, "1", ("b" * 64,),
        ("c" * 64,), UNIVERSE_ID, "trend-etf@2", "signal-parameters-v1", "git:abcdef0",
        SignalValidity(21, 21, NOW + timedelta(days=1), DecayProfile.LINEAR),
        digest(canonical(snapshot_values)),
    )
    return TimeSeriesObservation(
        signal_run_id=f"{100 + number:064x}", signal_id="trend.etf", signal_version="2",
        as_of=as_of, information_cutoff=as_of, embargo_until=as_of + timedelta(days=5),
        outcome_available_at=available_at or as_of + timedelta(days=5),
        universe_manifest_id=UNIVERSE_ID, forecast=Decimal(forecast), realized_return=Decimal(realized),
        implementation_cost=Decimal("0.001"), instrument_id="ETF",
        regime="risk_on" if number % 2 else "risk_off", drawdown=Decimal(f"-0.0{number}"),
        horizon_days=5, code_revision="git:abcdef0", signal_snapshot=snapshot,
    )


def test_cross_sectional_diagnostics_store_pit_metrics_and_post_cost_value(tmp_path):
    result = SignalDiagnosticsStore(ImmutableDatasetStore(tmp_path)).evaluate_cross_sectional(
        cross_observation(), config=DiagnosticConfig(transaction_cost_per_turnover=Decimal("0.001")),
        evaluated_at=NOW,
    )
    assert result.status == "READY" and result.reason_code == "OK" and result.catalog_id
    assert result.report is not None
    metrics = result.report.metrics
    assert metrics["coverage"] == "1"
    assert metrics["pearson_ic"] == "1" and metrics["spearman_rank_ic"] == "1"
    assert metrics["quantile_returns"] == {
        "Q1": "0.01", "Q2": "0.02", "Q3": "0.03", "Q4": "0.04", "Q5": "0.05",
    }
    assert metrics["gross_spread"] == "0.04"
    assert metrics["turnover"] == "1" and metrics["post_cost_spread"] == "0.038"
    assert metrics["holding_period_overlap"] == "0.6"
    stored = json.loads((tmp_path / "catalog" / "signal-diagnostics" / f"{result.catalog_id}.json").read_text())
    assert stored == result.report.payload()


@pytest.mark.parametrize("observation", [
    cross_observation(values={
        "AAA": Decimal("0.1"), "BBB": None, "CCC": None, "DDD": None, "EEE": None,
    }),
    cross_observation(number=2, available_at=NOW + timedelta(days=1)),
])
def test_cross_sectional_leakage_and_coverage_fail_closed(tmp_path, observation):
    result = SignalDiagnosticsStore(ImmutableDatasetStore(tmp_path)).evaluate_cross_sectional(
        observation, config=DiagnosticConfig(), evaluated_at=NOW,
    )
    assert result.status == "ABSTAIN" and result.report is None and result.catalog_id is None
    assert result.reason_code in {"DIAGNOSTIC_COVERAGE_INSUFFICIENT", "DIAGNOSTIC_OUTCOME_NOT_AVAILABLE"}


def test_pit_universe_and_embargo_contract_cannot_be_fabricated():
    observation = cross_observation()
    with pytest.raises(InvariantViolation, match="DIAGNOSTIC_UNIVERSE_PIT_INVALID"):
        replace(observation, forward_returns={"AAA": Decimal("0.01")})
    with pytest.raises(InvariantViolation, match="DIAGNOSTIC_TEMPORAL_ORDER_INVALID"):
        replace(observation, embargo_until=observation.as_of)
    with pytest.raises(InvariantViolation, match="DIAGNOSTIC_SIGNAL_SNAPSHOT_MISMATCH"):
        replace(observation, signal_snapshot=replace(observation.signal_snapshot, signal_version="forged"))


def test_ic_decay_stores_separate_horizon_metrics(tmp_path):
    observations = (
        cross_observation(number=1, horizon=5), cross_observation(number=2, horizon=5),
        cross_observation(number=3, horizon=20), cross_observation(number=4, horizon=20),
    )
    result = SignalDiagnosticsStore(ImmutableDatasetStore(tmp_path)).evaluate_ic_decay(
        observations, config=DiagnosticConfig(), evaluated_at=NOW,
    )
    assert result.status == "READY" and result.report is not None
    assert result.report.metrics["horizon_ic_decay"]["5"]["observation_count"] == 2
    assert result.report.metrics["horizon_ic_decay"]["20"]["pearson_ic"] == "1"


def test_time_series_prioritizes_calibration_utility_and_stability(tmp_path):
    result = SignalDiagnosticsStore(ImmutableDatasetStore(tmp_path)).evaluate_time_series(
        (
            time_observation(1, "0.01", "0.011"),
            time_observation(2, "0.02", "0.019"),
            time_observation(3, "0.03", "0.035"),
        ),
        config=DiagnosticConfig(), evaluated_at=NOW,
    )
    assert result.status == "READY" and result.report is not None
    metrics = result.report.metrics
    assert metrics["sign_accuracy"] == "1"
    assert Decimal(metrics["mean_absolute_error"]) == Decimal("0.007") / Decimal(3)
    assert set(metrics["rolling_correlation"]) == {time_observation(1, "0.01", "0.011").as_of.isoformat()}
    assert set(metrics["regime_stability"]) == {"risk_on", "risk_off"}
    assert "post_cost_utility" in metrics and "drawdown_error_correlation" in metrics


def test_time_series_future_outcome_and_forged_signal_fail_closed(tmp_path):
    store = SignalDiagnosticsStore(ImmutableDatasetStore(tmp_path))
    result = store.evaluate_time_series(
        (
            time_observation(1, "0.01", "0.011", available_at=NOW + timedelta(days=1)),
            time_observation(2, "0.02", "0.019"),
            time_observation(3, "0.03", "0.035"),
        ),
        config=DiagnosticConfig(), evaluated_at=NOW,
    )
    assert (result.status, result.reason_code, result.report, result.catalog_id) == (
        "ABSTAIN", "DIAGNOSTIC_OUTCOME_NOT_AVAILABLE", None, None,
    )
    observation = time_observation(1, "0.01", "0.011")
    with pytest.raises(InvariantViolation, match="DIAGNOSTIC_SIGNAL_SNAPSHOT_MISMATCH"):
        replace(observation, signal_snapshot=replace(observation.signal_snapshot, signal_version="forged"))


def test_diagnostic_schema_matches_report_contract(tmp_path):
    result = SignalDiagnosticsStore(ImmutableDatasetStore(tmp_path)).evaluate_cross_sectional(
        cross_observation(), config=DiagnosticConfig(), evaluated_at=NOW,
    )
    assert result.report is not None
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/signal_diagnostics.schema.json").read_text())
    assert set(schema["required"]) == set(result.report.payload())
