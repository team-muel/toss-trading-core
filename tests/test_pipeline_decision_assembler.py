from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from asset_management.config.versions import content_hash
from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.decisions.governor import target_weight_hash
from asset_management.domain.errors import InvariantViolation
from asset_management.orchestration import (
    DecisionKernel, DecisionRuntime, DecisionRuntimeAdapter, PricingApplicabilityEvidence,
    RuntimeAdapterDescriptor,
)
from asset_management.orchestration.pipelines import (
    PipelineEvidenceRepository, PipelineStage, StageEvidence,
)
from asset_management.time.clock import FrozenClock


ROOT = Path(__file__).parents[1]
NOW = "2026-09-10T00:00:00+00:00"
CUTOFF = "2026-09-09T23:59:00+00:00"


def hashed(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")), content_hash(payload)


def evidence():
    return PricingApplicabilityEvidence.create(
        scope_key="asset-class:equity", applicable=True, reason=None,
        policy_version="pricing-applicability@1",
    )


def descriptor():
    return RuntimeAdapterDescriptor(
        DecisionRuntime.PAPER, "clock@1", "data@1", "broker@1", "execution@1", "persistence@1",
    )


def repository_with_verified_artifacts():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    Migrator(conn, FrozenClock(datetime(2026, 9, 10, tzinfo=timezone.utc))).migrate(
        load_migration_catalog(ROOT / "schemas")
    )
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)", ("run@1", NOW, CUTOFF, "git:abc", NOW))

    investment_payload, investment_hash = hashed({"kind": "investment"})
    risk_policy_payload, risk_policy_hash = hashed({"kind": "risk"})
    conn.execute("INSERT INTO am_policy_version VALUES (?, 'investment', ?, NULL, 'approver', 'approved', ?, ?)",
                 ("investment@1", CUTOFF, investment_payload, investment_hash))
    conn.execute("INSERT INTO am_policy_version VALUES (?, 'risk', ?, NULL, 'approver', 'approved', ?, ?)",
                 ("risk@1", CUTOFF, risk_policy_payload, risk_policy_hash))
    parameter_payload, parameter_hash = hashed({"parameters": "v1"})
    conn.execute("INSERT INTO am_parameter_set VALUES ('params@1', ?, ?, ?)",
                 (NOW, parameter_payload, parameter_hash))

    conn.execute("INSERT INTO am_raw_api_response VALUES ('raw@1', 'broker', '/accounts', 'GET', 'request', 200, 'response', '{}', ?, ?, 'account@1', 'v1', '{}')",
                 (CUTOFF, CUTOFF))
    account_payload, account_hash = hashed({"cash": "100"})
    conn.execute("INSERT INTO am_account_snapshot VALUES ('account@1', 'run@1', 'account@1', ?, 'raw@1', ?, ?)",
                 (CUTOFF, account_payload, account_hash))
    conn.execute("INSERT INTO am_account_snapshot_raw VALUES ('account@1', 'raw@1')")
    conn.execute("INSERT INTO am_ingestion_run VALUES ('ingestion@1', 'run@1', 'provider', ?, ?)", (CUTOFF, NOW))
    data_hash = "a" * 64
    conn.execute("INSERT INTO am_dataset_manifest VALUES ('manifest@1', 'ingestion@1', 'gold', 'prices', 'memory://prices', ?, ?, ?, 'v1', 1)",
                 (data_hash, CUTOFF, CUTOFF))

    feature_payload, feature_hash = hashed({"feature_values": {"quality": "0.7"}})
    conn.execute("INSERT INTO am_feature_run VALUES ('feature@1', 'run@1', 'manifest@1', 'feature@1', ?, ?)",
                 (feature_payload, feature_hash))
    state_payload, state_hash = hashed({"signal_values": {"SPY": "0.4"}})
    conn.execute("INSERT INTO am_state_run VALUES ('state@1', 'feature@1', 'state@1', ?, ?)",
                 (state_payload, state_hash))
    pricing_authority = evidence()
    pricing_payload, pricing_hash = hashed({
        "pricing_outputs": {"pricing-baseline": "0.06"}, "pricing_applicable": True,
        "pricing_non_applicability_reason": None,
        "pricing_applicability_evidence_id": pricing_authority.evidence_id,
    })
    conn.execute("INSERT INTO am_pricing_run VALUES ('pricing@1', 'state@1', 'pricing@1', ?, ?)",
                 (pricing_payload, pricing_hash))
    expectation_payload, expectation_hash = hashed({
        "strategy_key": "quality-momentum@1", "forecast_values": {"SPY": "0.08"},
    })
    conn.execute("INSERT INTO am_expectation_run VALUES ('expectation@1', 'pricing@1', 'expectation@1', ?, ?)",
                 (expectation_payload, expectation_hash))
    risk_payload, risk_hash = hashed({"risk_outputs": {"volatility": "0.12"}})
    conn.execute("INSERT INTO am_risk_model_run VALUES ('risk-model@1', 'state@1', 'risk@1', ?, ?)",
                 (risk_payload, risk_hash))
    weights = {"SPY": Decimal("0.6"), "CASH": Decimal("0.4")}
    target_payload, target_hash = hashed({
        "target_weights": {key: str(value) for key, value in weights.items()},
        "target_weight_hash": target_weight_hash(weights),
        "order_intent_economics": {"objective": "rebalance@1"},
    })
    conn.execute("INSERT INTO am_portfolio_target VALUES ('target@1', 'expectation@1', 'risk-model@1', 'investment@1', 'params@1', ?, ?)",
                 (target_payload, target_hash))
    risk_decision_hash = "b" * 64
    conn.execute("INSERT INTO am_risk_decision VALUES ('decision@1', 'target@1', 'ALLOW', '[]', 'risk@1', ?)",
                 (risk_decision_hash,))

    repository = PipelineEvidenceRepository(conn)
    time_hash = content_hash({
        "runtime_run_id": "run@1", "as_of_utc": NOW, "information_cutoff_utc": CUTOFF,
        "code_revision": "git:abc",
    })
    for stage, identifier, artifact_hash in (
        (PipelineStage.INVESTMENT_POLICY, "investment@1", investment_hash),
        (PipelineStage.ACCOUNT_TRUTH, "account@1", account_hash),
        (PipelineStage.TIME_TRUTH, "run@1", time_hash),
        (PipelineStage.DATA_TRUTH, "manifest@1", data_hash),
        (PipelineStage.FINANCIAL_CALCULATION, "expectation@1", expectation_hash),
        (PipelineStage.TARGET_PORTFOLIO, "target@1", target_hash),
        (PipelineStage.RISK_CONTROL, "decision@1", risk_decision_hash),
    ):
        repository.record("run@1", StageEvidence(stage, identifier, artifact_hash))
    return repository, pricing_authority


def test_production_assembler_uses_only_verified_persisted_economic_values():
    repository, pricing_authority = repository_with_verified_artifacts()
    request = repository.assemble_canonical_decision_request(
        "run@1", pricing_applicability_evidence=pricing_authority,
    )

    result = DecisionRuntimeAdapter(DecisionKernel("decision-kernel@1"), descriptor()).decide(request)
    assert result.decision.forecast_values == {"SPY": Decimal("0.08")}
    assert result.decision.target_weights == {"CASH": Decimal("0.4"), "SPY": Decimal("0.6")}
    assert result.decision.risk_decision_id == "decision@1"
    with pytest.raises(TypeError):
        repository.assemble_canonical_decision_request(
            "run@1", pricing_applicability_evidence=pricing_authority,
            forecast_values={"SPY": Decimal("99")},
        )


def test_tampered_assembled_economic_value_and_persisted_hash_conflict_fail_closed():
    repository, pricing_authority = repository_with_verified_artifacts()
    request = repository.assemble_canonical_decision_request(
        "run@1", pricing_applicability_evidence=pricing_authority,
    )
    adapter = DecisionRuntimeAdapter(DecisionKernel("decision-kernel@1"), descriptor())
    with pytest.raises(InvariantViolation, match="CANONICAL_DECISION_ASSEMBLY_TAMPERED"):
        adapter.decide(replace(request, forecast_values={"SPY": Decimal("99")}))

    repository._conn.execute("PRAGMA foreign_keys=OFF")
    repository._conn.execute("UPDATE am_expectation_run SET payload_json=? WHERE expectation_run_id='expectation@1'",
                             (json.dumps({"strategy_key": "quality-momentum@1", "forecast_values": {"SPY": "99"}}),))
    with pytest.raises(InvariantViolation, match="CANONICAL_ASSEMBLER_EXPECTATION_PAYLOAD_INVALID"):
        repository.assemble_canonical_decision_request("run@1", pricing_applicability_evidence=pricing_authority)
