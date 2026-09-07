from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from asset_management.decisions.economic_journal import (
    DECISION_JOURNAL_SCHEMA_VERSION, DecisionOutcomeEvent, DecisionQuality, EconomicDecisionJournal,
    EconomicDecisionRecord, ReturnMetric, ReturnMetricStatus, ReturnSemanticType, ReturnUnit,
    RiskContributionType,
    classify_decision_quality,
)
from asset_management.decisions.governor import DecisionState
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import (BenchmarkDefinition, InvestorMandate, InvestorMandateRegistry,
                                         MandateObjective, RiskPreference, WealthConvention)
from asset_management.portfolio import PortfolioTarget
from asset_management.risk.models import CurrencyBasis
from asset_management.states.models import StateType


D = Decimal
NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


UNITS = {
    ReturnSemanticType.PRICING_BASELINE_RETURN: ReturnUnit.TOTAL_RETURN,
    ReturnSemanticType.FORECAST_TOTAL_RETURN_GROSS: ReturnUnit.TOTAL_RETURN,
    ReturnSemanticType.FORECAST_TOTAL_RETURN_NET: ReturnUnit.TOTAL_RETURN,
    ReturnSemanticType.MODEL_RELATIVE_ALPHA: ReturnUnit.EXCESS_RETURN,
    ReturnSemanticType.EXPECTED_BENCHMARK_ACTIVE_RETURN: ReturnUnit.ACTIVE_RETURN,
    ReturnSemanticType.REALIZED_ACTIVE_RETURN: ReturnUnit.ACTIVE_RETURN,
    ReturnSemanticType.REGRESSION_ALPHA: ReturnUnit.REGRESSION_INTERCEPT,
}


def metric(kind, value=None, status=None):
    status = status or (ReturnMetricStatus.AVAILABLE if value is not None else ReturnMetricStatus.NOT_APPLICABLE)
    return ReturnMetric(kind, status, value, CurrencyBasis.BASE, 21, UNITS[kind],
                        f"{kind.value.lower()}-formula@1", f"{kind.value.lower()}-model@1")


def return_metrics(objective=MandateObjective.BENCHMARK_RELATIVE):
    return (
        metric(ReturnSemanticType.PRICING_BASELINE_RETURN, D(".04")),
        metric(ReturnSemanticType.FORECAST_TOTAL_RETURN_GROSS, D(".10")),
        metric(ReturnSemanticType.FORECAST_TOTAL_RETURN_NET, D(".08")),
        metric(ReturnSemanticType.MODEL_RELATIVE_ALPHA, status=ReturnMetricStatus.NOT_APPLICABLE),
        metric(ReturnSemanticType.EXPECTED_BENCHMARK_ACTIVE_RETURN,
               D(".02") if objective is not MandateObjective.ABSOLUTE_WEALTH else None,
               ReturnMetricStatus.AVAILABLE if objective is not MandateObjective.ABSOLUTE_WEALTH else ReturnMetricStatus.NOT_APPLICABLE),
        metric(ReturnSemanticType.REALIZED_ACTIVE_RETURN, status=ReturnMetricStatus.NOT_MATURED),
        metric(ReturnSemanticType.REGRESSION_ALPHA, status=ReturnMetricStatus.NOT_MATURED),
    )


def target(stage):
    return PortfolioTarget(("RISK", "CASH"), (D(".6"), D(".4")), stage)


def mandate_registry():
    registry = InvestorMandateRegistry()
    for identifier in ("SPY", "CASH"):
        registry.register_benchmark(BenchmarkDefinition(identifier, "2", identifier, True, "USD", 21,
                                                        "universe@1", NOW - timedelta(days=1),
                                                        NOW + timedelta(days=30)))
    preference = RiskPreference(D(".2"), D(".2"), D(".1"), D(".2"), D(".1"), D(".1"),
                                "concentration@1", "turnover@1", "tax@1", "liquidity@1", D(0), D(2),
                                D(0), D(2), "investor approval", ("authority:1",))
    registry.register_mandate(InvestorMandate("household", "1", MandateObjective.BENCHMARK_RELATIVE,
                                              "USD", "USD", WealthConvention.NOMINAL, 21, 21, 21,
                                              "SPY@2", None, "CASH@2", preference, NOW - timedelta(days=1),
                                              NOW + timedelta(days=30)))
    return registry


def record(**changes):
    values = {
        "run_id": "run-60", "as_of": NOW, "information_cutoff": NOW - timedelta(minutes=1),
        "assessment_horizon_end": NOW + timedelta(days=21), "mandate_key": "household@1",
        "mandate_version": "1", "benchmark_key": "SPY@2", "benchmark_version": "2",
        "objective": MandateObjective.BENCHMARK_RELATIVE, "reporting_currency": "USD",
        "risk_budget_version": "risk-budget@3",
        "risk_aversion_policy_version": "risk-aversion@2",
        "state_snapshot_ids": {item: f"state-{item.value.lower()}" for item in StateType},
        "pricing_lineage_ids": ("pricing:1",), "forecast_lineage_ids": ("forecast:1",),
        "risk_lineage_ids": ("risk:1",), "target_lineage_ids": ("target:1",),
        "decision_lineage_ids": ("decision:1",), "calculation_lineage_ids": ("calculation:1",),
        "return_metrics": return_metrics(), "risk_snapshot_id": "risk-snapshot:1",
        "risk_values": {"volatility": D(".12"), "cvar": D(".08")},
        "risk_contribution_type": RiskContributionType.VOLATILITY,
        "risk_contributions": {"RISK": D(".9"), "CASH": D(".1")},
        "raw_target": target("RAW_TARGET"), "constrained_target": target("RISK_CONSTRAINED_TARGET"),
        "executable_target": target("EXECUTABLE_TARGET"), "risk_decision_id": "risk-decision:1",
        "risk_decision_state": DecisionState.REDUCE, "risk_reason_codes": ("VOLATILITY_HIGH",),
        "policy_versions": {"execution": "execution@1"}, "parameter_versions": {"optimizer": "params@1"},
        "model_versions": {"pricing": "capm@2"}, "code_revision": "git:abcdef0",
    }
    values.update(changes)
    return EconomicDecisionRecord(**values)


def outcome(decision, **changes):
    metrics = {item.semantic_type: item for item in decision.return_metrics}
    values = {
        "decision_id": decision.decision_id, "decision_content_hash": decision.content_hash,
        "assessed_at": decision.assessment_horizon_end,
        "realized_active_return": replace(metrics[ReturnSemanticType.REALIZED_ACTIVE_RETURN],
                                            status=ReturnMetricStatus.AVAILABLE, value=D(".01")),
        "regression_alpha": replace(metrics[ReturnSemanticType.REGRESSION_ALPHA],
                                      status=ReturnMetricStatus.AVAILABLE, value=D(".005")),
        "process_good": True, "outcome_good": False,
        "quality": DecisionQuality.GOOD_DECISION_BAD_OUTCOME,
    }
    values.update(changes)
    return DecisionOutcomeEvent(**values)


def test_record_preserves_four_states_lineage_three_targets_and_noncollapsed_return_semantics():
    item = record()
    assert set(item.state_snapshot_ids) == {item.value for item in StateType}
    assert item.raw_target.stage == "RAW_TARGET" and item.executable_target.stage == "EXECUTABLE_TARGET"
    values = {metric.semantic_type: metric for metric in item.return_metrics}
    assert set(values) == set(ReturnSemanticType)
    assert values[ReturnSemanticType.FORECAST_TOTAL_RETURN_GROSS].value == D(".10")
    assert values[ReturnSemanticType.FORECAST_TOTAL_RETURN_NET].value == D(".08")
    assert values[ReturnSemanticType.MODEL_RELATIVE_ALPHA].status is ReturnMetricStatus.NOT_APPLICABLE
    assert values[ReturnSemanticType.REALIZED_ACTIVE_RETURN].status is ReturnMetricStatus.NOT_MATURED
    assert item.payload()["mandate_version"] == "1" and item.payload()["benchmark_version"] == "2"
    assert item.decision_id == f"decision-{item.content_hash}"


def test_journal_replays_exact_initial_record_and_keeps_outcome_pending_before_maturity(tmp_path):
    journal = EconomicDecisionJournal(tmp_path / "economic-decisions.jsonl", mandate_registry())
    item = record()
    journal.append(item)
    journal.append(item)
    assert journal.records() == (item,)
    assert journal.quality_at(item.decision_id, at=NOW) is DecisionQuality.NOT_MATURED
    assert journal.quality_at(item.decision_id, at=item.assessment_horizon_end) is DecisionQuality.PENDING_OUTCOME


def test_mature_outcome_is_a_separate_event_and_quality_keeps_process_and_outcome_distinct(tmp_path):
    journal = EconomicDecisionJournal(tmp_path / "economic-decisions.jsonl", mandate_registry())
    item = record(); journal.append(item)
    event = outcome(item)
    journal.append_outcome(event)
    journal.append_outcome(event)
    assert journal.outcomes() == (event,)
    assert journal.quality_at(item.decision_id, at=event.assessed_at) is DecisionQuality.GOOD_DECISION_BAD_OUTCOME
    assert classify_decision_quality(process_good=False, outcome_good=True) is DecisionQuality.BAD_DECISION_GOOD_OUTCOME


def test_pre_maturity_or_semantically_changed_outcomes_fail_closed(tmp_path):
    journal = EconomicDecisionJournal(tmp_path / "economic-decisions.jsonl", mandate_registry())
    item = record(); journal.append(item)
    with pytest.raises(InvariantViolation, match="DECISION_OUTCOME_NOT_MATURED"):
        journal.append_outcome(outcome(item, assessed_at=NOW + timedelta(days=20)))
    baseline = next(value for value in item.return_metrics
                    if value.semantic_type is ReturnSemanticType.REGRESSION_ALPHA)
    bad_metric = replace(baseline, status=ReturnMetricStatus.AVAILABLE, value=D(".01"),
                         formula_version="different-formula@1")
    with pytest.raises(InvariantViolation, match="DECISION_OUTCOME_SEMANTIC_MISMATCH"):
        journal.append_outcome(outcome(item, regression_alpha=bad_metric))


def test_benchmark_relative_and_absolute_mandates_cannot_fabricate_or_reinterpret_active_returns():
    metrics = list(return_metrics())
    active = next(index for index, item in enumerate(metrics)
                  if item.semantic_type is ReturnSemanticType.EXPECTED_BENCHMARK_ACTIVE_RETURN)
    metrics[active] = metric(ReturnSemanticType.EXPECTED_BENCHMARK_ACTIVE_RETURN,
                             status=ReturnMetricStatus.NOT_APPLICABLE)
    with pytest.raises(InvariantViolation, match="DECISION_BENCHMARK_RETURN_SEMANTICS_INVALID"):
        record(return_metrics=tuple(metrics))
    absolute = record(objective=MandateObjective.ABSOLUTE_WEALTH,
                      return_metrics=return_metrics(MandateObjective.ABSOLUTE_WEALTH))
    assert next(item for item in absolute.return_metrics if item.semantic_type is ReturnSemanticType.EXPECTED_BENCHMARK_ACTIVE_RETURN).status is ReturnMetricStatus.NOT_APPLICABLE


@pytest.mark.parametrize("changes, reason", [
    ({"information_cutoff": NOW + timedelta(seconds=1)}, "ECONOMIC_DECISION_TIME_ORDER_INVALID"),
    ({"assessment_horizon_end": NOW}, "ECONOMIC_DECISION_TIME_ORDER_INVALID"),
    ({"state_snapshot_ids": {}}, "ECONOMIC_DECISION_STATE_SET_INVALID"),
    ({"risk_values": {}}, "ECONOMIC_DECISION_RISK_INVALID"),
    ({"return_metrics": return_metrics()[:-1]}, "DECISION_RETURN_SET_INVALID"),
])
def test_missing_or_conflicting_decision_evidence_is_rejected(changes, reason):
    with pytest.raises(InvariantViolation, match=reason):
        record(**changes)


def test_replay_rejects_tampered_schema_or_hash(tmp_path):
    journal = EconomicDecisionJournal(tmp_path / "economic-decisions.jsonl", mandate_registry())
    item = record(); journal.append(item)
    raw = json.loads(journal.path.read_text(encoding="utf-8"))
    raw["payload"]["schema_version"] = "decision-economic-journal@2"
    journal.path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(InvariantViolation, match="ECONOMIC_DECISION_JOURNAL_RECORD_INVALID"):
        journal.records()


def test_registry_mandate_and_benchmark_versions_must_match_ama_124_authority(tmp_path):
    journal = EconomicDecisionJournal(tmp_path / "economic-decisions.jsonl", mandate_registry())
    with pytest.raises(InvariantViolation, match="ECONOMIC_DECISION_MANDATE_VERSION_MISMATCH"):
        journal.append(record(benchmark_version="1"))


def test_schema_covers_complete_versioned_decision_record():
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/decision_economic_journal.schema.json").read_text())
    assert set(schema["required"]) == set(record().payload())
    assert DECISION_JOURNAL_SCHEMA_VERSION == "decision-economic-journal@1"


def test_outcome_schema_covers_separate_matured_event():
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/decision_outcome.schema.json").read_text())
    assert set(schema["required"]) == set(outcome(record()).payload())
