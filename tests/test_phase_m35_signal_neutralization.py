from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.quality.models import QualityStatus
from asset_management.signals import (
    NeutralizationConfig, NeutralizationInput, SignalNeutralizer, SignalSnapshot,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
UNIVERSE = tuple(f"ETF{number:02}" for number in range(12))
UNIVERSE_ID = "a" * 64


def snapshot(signal_id, version, values):
    rendered = {key: str(value) for key, value in values.items()}
    return SignalSnapshot(
        digest(canonical({"signal_id": signal_id, "version": version, "values": rendered})),
        signal_id, version, "SIGNAL_VALUE", (NOW - timedelta(days=10)).isoformat(),
        (NOW - timedelta(days=10)).isoformat(), rendered, QualityStatus.VALID.value, "1",
        ("b" * 64,), ("c" * 64,), UNIVERSE_ID, f"{signal_id}@{version}", "params-v1",
        "git:abcdef0", SignalValidity(21, 21, NOW + timedelta(days=1), DecayProfile.LINEAR),
        digest(canonical(rendered)),
    )


def inputs(*, available_at=None):
    candidate_values = {item: Decimal(number) + Decimal(number % 7) / Decimal(10)
                        for number, item in enumerate(UNIVERSE, 1)}
    baseline_values = {item: Decimal(number * 2) + (Decimal(1) if number % 2 else Decimal(-1))
                       for number, item in enumerate(UNIVERSE, 1)}
    forward_values = {item: Decimal(number) / Decimal(100) + Decimal(number % 7) / Decimal(1000)
                      for number, item in enumerate(UNIVERSE, 1)}
    as_of = NOW - timedelta(days=10)
    return NeutralizationInput(
        candidate=snapshot("value.new", "1", candidate_values),
        baselines=(snapshot("value.existing", "1", baseline_values),),
        as_of=as_of, information_cutoff=as_of, embargo_until=as_of + timedelta(days=5),
        outcome_available_at=available_at or as_of + timedelta(days=5), universe=UNIVERSE,
        exposures={
            item: {"sector": "tech" if number % 2 else "energy",
                   "industry": "software" if number % 4 < 2 else "oil",
                   "size": "large" if number % 3 else "small",
                   "liquidity": "high" if number % 5 else "low",
                   "beta": Decimal("0.8") + Decimal(number % 3) / Decimal(10)}
            for number, item in enumerate(UNIVERSE, 1)
        },
        forward_returns=forward_values,
        oos_forecast_before={item: Decimal(number) / Decimal(110) for number, item in enumerate(UNIVERSE, 1)},
        oos_forecast_after=forward_values,
        turnover_before=Decimal("0.2"), turnover_after=Decimal("0.3"),
    )


def test_neutralization_preserves_pit_transform_lineage_and_incremental_metrics(tmp_path):
    result = SignalNeutralizer(ImmutableDatasetStore(tmp_path)).evaluate(
        inputs(), config=NeutralizationConfig(transaction_cost_per_turnover=Decimal("0.001")),
        evaluated_at=NOW,
    )
    assert result.status == "READY" and result.report is not None and result.catalog_id
    assert result.report["semantic_type"] == "RESIDUALIZED_SIGNAL_COMPONENT"
    assert result.report["transform_order"][-1] == "OLS_RESIDUAL"
    assert result.report["coverage_before"] == result.report["coverage_after"] == "1"
    assert "value.new@1" in result.report["signal_correlation_matrix"]
    assert Decimal(result.report["oos_mae_improvement"]) > 0
    assert Decimal(result.report["cost_after"]) > Decimal(result.report["cost_before"])


def test_future_outcome_and_snapshot_mismatch_fail_closed(tmp_path):
    neutralizer = SignalNeutralizer(ImmutableDatasetStore(tmp_path))
    result = neutralizer.evaluate(
        inputs(available_at=NOW + timedelta(days=1)), config=NeutralizationConfig(), evaluated_at=NOW,
    )
    assert (result.status, result.reason_code, result.report, result.catalog_id) == (
        "ABSTAIN", "NEUTRALIZATION_OUTCOME_NOT_AVAILABLE", None, None,
    )
    value = inputs()
    with pytest.raises(InvariantViolation, match="NEUTRALIZATION_SIGNAL_SNAPSHOT_MISMATCH"):
        replace(value, baselines=(replace(value.baselines[0], information_cutoff=NOW.isoformat()),))
