from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import InvariantViolation
from asset_management.signals import (
    ForecastCombinationParameters, ForecastCombinationRegistry, ForecastCombinationRequest,
    ForecastCombiner, ForecastSource,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
UNIVERSE = {"AAA": Decimal("0.03"), "BBB": Decimal("0.02")}


def source(number, signal, estimates, *, evidence=None):
    as_of = NOW - timedelta(days=1)
    return ForecastSource(
        forecast_calibration_id=f"{number:064x}", signal_run_id=f"{10 + number:064x}",
        neutralization_id=f"{20 + number:064x}", signal_id=signal, as_of=as_of,
        information_cutoff=as_of, oos_evidence_available_at=evidence or as_of - timedelta(hours=1),
        universe_manifest_id="a" * 64, currency="USD", unit="DECIMAL_RETURN", horizon=21,
        valid_until=NOW + timedelta(days=1), point_estimates=estimates,
        uncertainty=Decimal("0.02"), confidence=Decimal("0.8"),
        incremental_ic=Decimal("0.1") * number, stability=Decimal("0.9"),
        coverage=Decimal("0.95"), regime_sensitivity=Decimal("0.1"),
        turnover=Decimal("0.2"), implementation_cost=Decimal("0.001") * number,
    )


def parameters():
    return ForecastCombinationParameters(
        "forecast-overlay", "1", Decimal("0.7"), Decimal("1"),
        "forecast-combination@1", "forecast-combination-parameters-v1",
    )


def request():
    return ForecastCombinationRequest(
        (source(1, "value", UNIVERSE), source(2, "quality", {"AAA": Decimal("0.01"), "BBB": Decimal("0.04")})),
        ((Decimal("0.0004"), Decimal("0.0001")), (Decimal("0.0001"), Decimal("0.0004"))),
        ((Decimal(1), Decimal("0.25")), (Decimal("0.25"), Decimal(1))), NOW,
    )


def test_combiner_uses_neutralization_oos_cost_and_correlation(tmp_path):
    registry = ForecastCombinationRegistry([parameters()])
    result = ForecastCombiner(ImmutableDatasetStore(tmp_path), registry).combine(
        request(), combination_id="forecast-overlay", version="1",
    )
    assert result.status == "READY" and result.report is not None and result.catalog_id
    report = result.report
    assert set(report["neutralization_lineage_ids"]) == {f"{21:064x}", f"{22:064x}"}
    assert Decimal(report["effective_independent_forecasts"]) < Decimal(2)
    assert Decimal(report["expected_implementation_cost"]) > 0
    assert sum(Decimal(item["weight"]) for item in report["contributions"].values()) == Decimal(1)
    assert report["components"]["AAA"]["semantic_type"] == "combined_signal_forecast_component"


def test_future_oos_evidence_and_parameter_conflicts_fail_closed(tmp_path):
    with pytest.raises(InvariantViolation, match="FORECAST_COMBINATION_LINEAGE_OR_TIME_INVALID"):
        ForecastCombinationRequest(
            (source(1, "value", UNIVERSE, evidence=NOW + timedelta(days=1)),
             source(2, "quality", {"AAA": Decimal("0.01"), "BBB": Decimal("0.04")})),
            ((Decimal("0.0004"), Decimal("0.0001")), (Decimal("0.0001"), Decimal("0.0004"))),
            ((Decimal(1), Decimal("0.25")), (Decimal("0.25"), Decimal(1))), NOW,
        )
    registry = ForecastCombinationRegistry([parameters()])
    with pytest.raises(InvariantViolation, match="FORECAST_COMBINATION_PARAMETER_CONFLICT"):
        registry.register(replace(parameters(), cost_penalty=Decimal("2")))
    result = ForecastCombiner(ImmutableDatasetStore(tmp_path), registry).combine(
        request(), combination_id="unknown", version="1",
    )
    assert (result.status, result.reason_code, result.report, result.catalog_id) == (
        "ABSTAIN", "FORECAST_COMBINATION_PARAMETERS_UNKNOWN", None, None,
    )


def test_combination_schema_matches_published_contract(tmp_path):
    result = ForecastCombiner(ImmutableDatasetStore(tmp_path), ForecastCombinationRegistry([parameters()])).combine(
        request(), combination_id="forecast-overlay", version="1",
    )
    assert result.report is not None
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/forecast_combination.schema.json").read_text())
    assert set(schema["required"]) == set(result.report)
