from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.quality.models import QualityStatus
from asset_management.signals import (
    CalibrationSample, ForecastCalibrationConfig, ForecastCalibrationRequest,
    SignalForecastCalibrator, SignalSnapshot,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
UNIVERSE = ("AAA", "BBB", "CCC", "DDD", "EEE")
UNIVERSE_ID = "a" * 64


def snapshot(number, *, days_ago, multiplier=Decimal("1")):
    as_of = NOW - timedelta(days=days_ago)
    values = {item: str(Decimal(index) * multiplier) for index, item in enumerate(UNIVERSE, 1)}
    return SignalSnapshot(
        f"{number:064x}", "value.relative_strength", "1", "SIGNAL_VALUE", as_of.isoformat(),
        as_of.isoformat(), values, QualityStatus.VALID.value, "1", ("b" * 64,), ("c" * 64,),
        UNIVERSE_ID, "relative-strength@1", "signal-params-v1", "git:abcdef0",
        SignalValidity(21, 21, NOW + timedelta(days=2), DecayProfile.LINEAR), digest(canonical(values)),
    )


def sample(number, days_ago, multiplier):
    value = snapshot(number, days_ago=days_ago, multiplier=multiplier)
    returns = {item: Decimal(value.values[item]) / Decimal(100) for item in UNIVERSE}
    return CalibrationSample(
        value, returns, NOW - timedelta(days=days_ago - 1),
        "risk_on" if number % 2 else "risk_off",
        {item: "large" if index < 4 else "small" for index, item in enumerate(UNIVERSE, 1)},
    )


def request():
    return ForecastCalibrationRequest(
        training=(sample(1, 12, Decimal("1")), sample(2, 10, Decimal("1.1"))),
        validation=(sample(3, 8, Decimal("0.9")), sample(4, 6, Decimal("1.2"))),
        target_snapshot=snapshot(5, days_ago=1), target_universe=UNIVERSE, evaluated_at=NOW,
    )


def test_time_ordered_calibration_emits_cost_aware_forecast_components(tmp_path):
    result = SignalForecastCalibrator(ImmutableDatasetStore(tmp_path)).calibrate(
        request(), config=ForecastCalibrationConfig(forecast_cost_floor=Decimal("0.02")),
    )
    assert result.status == "READY" and result.report is not None and result.catalog_id
    report = result.report
    assert report["selected_mapping"] == "LINEAR"
    assert report["coverage"] == "1" and report["cost_floor_shrunk_count"] == 2
    assert set(report["components"]) == set(UNIVERSE)
    component = report["components"]["AAA"]
    assert component["semantic_type"] == "signal_forecast_return_component"
    assert component["currency"] == "USD" and component["horizon"] == 21
    assert Decimal(component["point_estimate"]) == Decimal("0")
    assert set(report["oos_calibration_curve"]) == {"Q1", "Q2", "Q3", "Q4", "Q5"}


def test_minimum_history_and_future_outcome_fail_closed(tmp_path):
    calibrator = SignalForecastCalibrator(ImmutableDatasetStore(tmp_path))
    result = calibrator.calibrate(
        request(), config=ForecastCalibrationConfig(minimum_training_samples=20),
    )
    assert (result.status, result.reason_code, result.report, result.catalog_id) == (
        "ABSTAIN", "FORECAST_CALIBRATION_TRAINING_HISTORY_INSUFFICIENT", None, None,
    )
    bad_validation = CalibrationSample(
        snapshot(9, days_ago=6), {item: Decimal("0.01") for item in UNIVERSE},
        NOW + timedelta(days=1), "risk_on", {item: "large" for item in UNIVERSE},
    )
    with pytest.raises(InvariantViolation, match="FORECAST_CALIBRATION_SAMPLE_LINEAGE_INVALID"):
        ForecastCalibrationRequest(
            training=(sample(1, 12, Decimal("1")), sample(2, 10, Decimal("1.1"))),
            validation=(bad_validation,), target_snapshot=snapshot(5, days_ago=1),
            target_universe=UNIVERSE, evaluated_at=NOW,
        )


def test_forecast_calibration_schema_matches_published_contract(tmp_path):
    result = SignalForecastCalibrator(ImmutableDatasetStore(tmp_path)).calibrate(
        request(), config=ForecastCalibrationConfig(),
    )
    assert result.report is not None
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/signal_forecast_calibration.schema.json").read_text())
    assert set(schema["required"]) == set(result.report)
