from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from alpha_management.expression import AlphaSimulationSettings
from alpha_management.history import HistoryPoint, HistorySimulationResult
from asset_management.domain.errors import InvariantViolation
from asset_management.orchestration.research_bridge import (
    CostTiming, DecayStage, GrossNetBasis, ResearchSignalBridgeContract,
    bridge_history_result, require_decay_stage_available,
)


NOW = datetime(2026, 9, 8, 1, tzinfo=timezone.utc)


def history_result(*, expression="ts_decay_linear(close, 5)", decay=3):
    point = HistoryPoint(
        effective_time_utc=NOW,
        signal_time_utc=NOW,
        information_cutoff_utc=NOW,
        raw={"SPY": 0.4, "QQQ": -0.2},
        base_weights={"SPY": 0.6, "QQQ": 0.4},
        weights={"SPY": 0.55, "QQQ": 0.45},
        dataset_manifest_ids=("a" * 64, "b" * 64),
        universe_version="universe@1",
        signal_universe_version="universe@1",
        source_run_id="research-run@1",
        code_revision="git:abcdef1",
        neutralization_groups={},
    )
    return HistorySimulationResult(
        expression=expression,
        expression_hash="c" * 64,
        settings=AlphaSimulationSettings(universe="US", decay=decay),
        points=(point,),
    )


def contract(**changes):
    values = dict(
        signal_id="research.fast-expression",
        version="1",
        currency="USD",
        forecast_horizon=21,
    )
    values.update(changes)
    return ResearchSignalBridgeContract(**values)


def test_bridge_uses_raw_research_score_not_simulated_position_or_return_semantics():
    record = bridge_history_result(history_result(), contract())

    assert record.semantic_type == "SIGNAL_VALUE"
    assert record.values == {"QQQ": Decimal("-0.2"), "SPY": Decimal("0.4")}
    assert record.gross_net_basis is GrossNetBasis.NOT_A_RETURN
    assert record.cost_timing is CostTiming.NOT_APPLICABLE
    assert record.consumed_decay_stages == (DecayStage.EXPRESSION,)
    assert record.observed_unconsumed_decay_stages == (DecayStage.RESEARCH_POSITION,)
    assert len(record.research_lineage_id) == 64


def test_bridge_does_not_consume_research_position_or_forecast_validity_decay():
    record = bridge_history_result(history_result(), contract())
    require_decay_stage_available(record, DecayStage.RESEARCH_POSITION)
    require_decay_stage_available(record, DecayStage.FORECAST_VALIDITY)
    with pytest.raises(InvariantViolation, match="DECAY_STAGE_ALREADY_CONSUMED"):
        require_decay_stage_available(record, DecayStage.EXPRESSION)
    with pytest.raises(InvariantViolation, match="IC_DECAY_IS_DIAGNOSTIC_NOT_TRANSFORMATION"):
        require_decay_stage_available(record, DecayStage.IC_DIAGNOSTIC)


def test_bridge_rejects_pretending_research_signal_is_gross_or_net_return():
    with pytest.raises(InvariantViolation, match="RESEARCH_SIGNAL_BRIDGE_CONTRACT_INVALID"):
        contract(gross_net_basis=GrossNetBasis.GROSS_RETURN)
    with pytest.raises(InvariantViolation, match="RESEARCH_SIGNAL_BRIDGE_CONTRACT_INVALID"):
        contract(cost_timing=CostTiming.AFTER_FORECAST)


def test_bridge_requires_complete_pit_lineage_and_finite_raw_scores():
    result = history_result(expression="rank(close)", decay=0)
    point = result.points[0]
    missing_lineage = HistorySimulationResult(
        expression=result.expression,
        expression_hash=result.expression_hash,
        settings=result.settings,
        points=(HistoryPoint(
            effective_time_utc=point.effective_time_utc,
            signal_time_utc=point.signal_time_utc,
            information_cutoff_utc=point.information_cutoff_utc,
            raw=point.raw,
            base_weights=point.base_weights,
            weights=point.weights,
            dataset_manifest_ids=(),
            universe_version=point.universe_version,
            signal_universe_version=point.signal_universe_version,
            source_run_id=point.source_run_id,
            code_revision=point.code_revision,
            neutralization_groups=point.neutralization_groups,
        ),),
    )
    with pytest.raises(InvariantViolation, match="RESEARCH_SIGNAL_BRIDGE_LINEAGE_INVALID"):
        bridge_history_result(missing_lineage, contract())


def test_signal_core_does_not_import_alpha_research_package():
    for path in Path("src/asset_management/signals").rglob("*.py"):
        assert "alpha_management" not in path.read_text(encoding="utf-8"), path
