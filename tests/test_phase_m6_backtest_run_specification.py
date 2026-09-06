from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import (
    BenchmarkDefinition, InvestorMandate, InvestorMandateRegistry, MandateObjective,
    RiskPreference, WealthConvention,
)
from asset_management.validation import (
    BacktestPeriod, BacktestRunRegistry, BacktestRunSpec, BacktestRunStatus,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
MANIFESTS = ("a" * 64, "b" * 64)
OUTPUT_HASH = "c" * 64


def mandate_registry():
    registry = InvestorMandateRegistry()
    for identifier in ("SPY", "CASH"):
        registry.register_benchmark(BenchmarkDefinition(
            identifier, "1", identifier, True, "USD", 21, "etf-us-v1", NOW,
            NOW + timedelta(days=1),
        ))
    preference = RiskPreference(
        Decimal(".2"), Decimal(".25"), Decimal(".1"), Decimal(".2"), Decimal(".1"),
        Decimal(".1"), "concentration@1", "turnover@1", "tax@1", "liquidity@1",
        Decimal(0), Decimal(5), Decimal(0), Decimal(5), "investor approval", ("evidence:1",),
    )
    mandate = InvestorMandate(
        "household", "1", MandateObjective.BENCHMARK_RELATIVE, "USD", "USD",
        WealthConvention.NOMINAL, 63, 21, 21, "SPY@1", None, "CASH@1", preference,
        NOW, NOW + timedelta(days=1),
    )
    registry.register_mandate(mandate)
    return registry


def spec(**changes):
    values = dict(
        hypothesis_id="quality-value-v1", experiment_id="wf-2026-09", run_version="1",
        strategy_key="quality-value@1", mandate_key="household@1",
        objective=MandateObjective.BENCHMARK_RELATIVE, reporting_currency="USD",
        universe_version="etf-us@1", pit_universe_rule_key="pit-universe@1",
        universe_manifest_id=MANIFESTS[0], dataset_manifest_ids=MANIFESTS,
        information_cutoff=NOW,
        train_period=BacktestPeriod(NOW - timedelta(days=720), NOW - timedelta(days=240)),
        validation_period=BacktestPeriod(NOW - timedelta(days=240), NOW - timedelta(days=120)),
        test_period=BacktestPeriod(NOW - timedelta(days=120), NOW - timedelta(days=1)),
        forecast_horizon=21, holding_horizon=21, rebalance_horizon=21,
        benchmark_key="SPY@1", benchmark_total_return=True, benchmark_currency="USD",
        benchmark_rebalance_horizon=21, primary_oos_objective="active-return@1",
        strategic_risk_budget=Decimal(".2"), active_risk_budget=Decimal(".1"),
        tracking_error_budget=Decimal(".1"), max_stress_loss=Decimal(".25"),
        risk_aversion_policy_key="risk-aversion@1", risk_aversion_min=Decimal(0),
        risk_aversion_max=Decimal(5), active_risk_aversion_min=Decimal(0),
        active_risk_aversion_max=Decimal(5), hard_constraint_policy_key="constraints@1",
        acceptance_thresholds={"minimum_ir": Decimal(".3"), "max_drawdown": Decimal("-.2")},
        transaction_cost_model_key="cost@1", tax_model_key="tax@1", fx_model_key="fx@1",
        execution_fidelity_model_key="execution@1", parameter_search_space_key="grid@1",
        parameter_search_budget=20, purge_observations=21, embargo_observations=5,
        robustness_test_keys=("bootstrap@1", "perturbation@1"), seed_policy_key="fixed-7@1",
        code_revision="f6bd5f0",
    )
    values.update(changes)
    return BacktestRunSpec(**values)


def test_preregistered_spec_is_immutable_and_terminal_failure_is_persisted(tmp_path):
    ledger = BacktestRunRegistry(mandate_registry())
    value = spec()
    assert ledger.preregister(value) == value.spec_hash
    started = ledger.start(value.key, at=NOW + timedelta(seconds=1), evidence_ids=("plan:1",))
    failed = ledger.record_outcome(
        value.key, status=BacktestRunStatus.FAILED, at=NOW + timedelta(seconds=2),
        evidence_ids=("trace:1",), output_hash=OUTPUT_HASH, reason_code="DATA_GAP",
    )
    payload = ledger.payload()
    assert started.spec_hash == failed.spec_hash == value.spec_hash
    assert payload["events"][-1]["status"] == "FAILED"
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    identifier = ledger.publish(store)
    assert identifier == ledger.publish(store)
    assert json.loads((tmp_path / "catalog" / "backtest-run-registry" / f"{identifier}.json").read_text()) == payload


def test_changed_run_contract_requires_new_version_and_explicit_parent_lineage():
    ledger = BacktestRunRegistry(mandate_registry())
    first = spec()
    ledger.preregister(first)
    with pytest.raises(InvariantViolation, match="BACKTEST_RUN_SPEC_CONFLICT"):
        ledger.preregister(replace(first, acceptance_thresholds={"minimum_ir": Decimal(".5")}))
    with pytest.raises(TypeError):
        first.acceptance_thresholds["minimum_ir"] = Decimal(".5")
    successor = replace(
        first, run_version="2", parent_spec_hash=first.spec_hash,
        acceptance_thresholds={"minimum_ir": Decimal(".5")},
    )
    assert ledger.preregister(successor) == successor.spec_hash


def test_missing_manifest_mandate_substitution_and_outcome_overwrite_fail_closed():
    with pytest.raises(InvariantViolation, match="BACKTEST_UNIVERSE_MANIFEST_MISSING"):
        spec(universe_manifest_id="d" * 64)
    ledger = BacktestRunRegistry(mandate_registry())
    with pytest.raises(InvariantViolation, match="BACKTEST_RUN_SPEC_MANDATE_MISMATCH"):
        ledger.preregister(spec(strategic_risk_budget=Decimal(".19")))
    value = spec()
    ledger.preregister(value)
    with pytest.raises(InvariantViolation, match="BACKTEST_RUN_NOT_ACTIVE"):
        ledger.record_outcome(value.key, status=BacktestRunStatus.COMPLETED, at=NOW,
                              evidence_ids=("result:1",), output_hash=OUTPUT_HASH, reason_code="OK")
    ledger.start(value.key, at=NOW, evidence_ids=("plan:1",))
    ledger.record_outcome(value.key, status=BacktestRunStatus.INTERRUPTED, at=NOW + timedelta(seconds=1),
                          evidence_ids=("trace:1",), output_hash=OUTPUT_HASH, reason_code="WORKER_STOPPED")
    with pytest.raises(InvariantViolation, match="BACKTEST_RUN_NOT_ACTIVE"):
        ledger.record_outcome(value.key, status=BacktestRunStatus.COMPLETED, at=NOW + timedelta(seconds=2),
                              evidence_ids=("result:2",), output_hash=OUTPUT_HASH, reason_code="OK")


def test_schema_covers_published_specification_and_outcome_evidence():
    ledger = BacktestRunRegistry(mandate_registry())
    value = spec()
    ledger.preregister(value)
    ledger.start(value.key, at=NOW, evidence_ids=("plan:1",))
    ledger.record_outcome(value.key, status=BacktestRunStatus.COMPLETED, at=NOW + timedelta(seconds=1),
                          evidence_ids=("result:1",), output_hash=OUTPUT_HASH, reason_code="OK")
    schema = json.loads((Path(__file__).parents[1] / "schemas/backtest_run_specification.schema.json").read_text())
    payload = ledger.payload()
    assert set(schema["required"]) == set(payload)
    assert set(schema["$defs"]["spec"]["required"]) == set(payload["specs"][0])
    assert set(schema["$defs"]["event"]["required"]) == set(payload["events"][0])
