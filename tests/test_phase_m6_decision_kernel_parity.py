from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.decisions.governor import DecisionState
from asset_management.domain.errors import InvariantViolation
from asset_management.orchestration import (
    DecisionKernel, DecisionParityLedger, DecisionRuntime, DecisionRuntimeAdapter,
    FrozenDecisionInput, PreExecutionDecision, PricingApplicabilityEvidence,
    RuntimeAdapterDescriptor,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
PRICING_EVIDENCE = PricingApplicabilityEvidence.create(
    scope_key="quality-value@1", applicable=True, reason=None,
    policy_version="pricing-applicability@1",
)
NON_APPLICABLE_EVIDENCE = PricingApplicabilityEvidence.create(
    scope_key="quality-value@1", applicable=False,
    reason="asset-class-has-no-approved-pricing-model",
    policy_version="pricing-applicability@1",
)
MANIFESTS = ("a" * 64, PRICING_EVIDENCE.evidence_id)
LINEAGE = ("c" * 64,)


def frozen_input(**changes):
    values = dict(
        snapshot_id="snapshot@1", strategy_key="quality-value@1", model_keys=("capm@1",),
        policy_versions={"investment": "investment@1", "risk": "risk@1",
                         "pricing_applicability": PRICING_EVIDENCE.policy_version},
        parameter_set_key="parameters@1", input_manifest_ids=MANIFESTS,
        as_of=NOW, information_cutoff=NOW - timedelta(seconds=1), code_revision="b6dfe93",
        pricing_applicability_evidence=PRICING_EVIDENCE,
    )
    values.update(changes)
    return FrozenDecisionInput(**values)


def decision(**changes):
    values = dict(
        feature_values={"quality@1": Decimal("1.2")}, signal_values={"quality-signal@1": Decimal(".4")},
        forecast_values={"SPY": Decimal(".08")}, pricing_outputs={"pricing-baseline": Decimal(".06")},
        risk_outputs={"volatility": Decimal(".12")}, target_weights={"SPY": Decimal(".6"), "CASH": Decimal(".4")},
        risk_decision_id="risk-1", risk_decision_hash="d" * 64, risk_state=DecisionState.ALLOW,
        risk_reason_codes=(), order_intent_economics={"objective": "rebalance-to-target@1"},
        data_lineage_ids=LINEAGE, calculation_lineage_ids=("e" * 64,),
        pricing_applicability_evidence_id=PRICING_EVIDENCE.evidence_id,
    )
    values.update(changes)
    return PreExecutionDecision(**values)


def adapter(runtime):
    return RuntimeAdapterDescriptor(
        runtime, f"clock-{runtime.value}@1", f"data-{runtime.value}@1",
        f"broker-{runtime.value}@1", f"execution-{runtime.value}@1", f"persistence-{runtime.value}@1",
    )


def test_one_kernel_produces_identical_pre_execution_semantics_for_all_runtimes(tmp_path):
    inputs = frozen_input()
    kernel = DecisionKernel("decision-kernel@1", lambda _: decision())
    ledger = DecisionParityLedger()
    results = [ledger.record(DecisionRuntimeAdapter(kernel, adapter(runtime)).decide(inputs))
               for runtime in DecisionRuntime]
    assert len({item.semantic_hash for item in results}) == 1
    assert ledger.require_parity(inputs.input_hash) == results[0].semantic_hash
    assert results[0].decision.forecast_values["SPY"] == Decimal(".08")
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    identifier = ledger.publish(store)
    assert identifier == ledger.publish(store)
    assert json.loads((tmp_path / "catalog" / "decision-parity-ledger" / f"{identifier}.json").read_text()) == ledger.payload()


def test_runtime_specific_semantic_change_and_evidence_overwrite_fail_closed():
    inputs = frozen_input()
    ledger = DecisionParityLedger()
    standard = DecisionKernel("decision-kernel@1", lambda _: decision())
    ledger.record(DecisionRuntimeAdapter(standard, adapter(DecisionRuntime.HISTORICAL_REPLAY)).decide(inputs))
    divergent = DecisionKernel("decision-kernel@1", lambda _: decision(risk_outputs={"volatility": Decimal(".20")}))
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_PARITY_MISMATCH"):
        ledger.record(DecisionRuntimeAdapter(divergent, adapter(DecisionRuntime.PAPER)).decide(inputs))
    with pytest.raises(InvariantViolation, match="DECISION_RUNTIME_EVIDENCE_CONFLICT"):
        ledger.record(DecisionRuntimeAdapter(divergent, adapter(DecisionRuntime.HISTORICAL_REPLAY)).decide(inputs))
    with pytest.raises(InvariantViolation, match="DECISION_PARITY_EVIDENCE_INCOMPLETE"):
        ledger.require_parity(inputs.input_hash)


def test_missing_frozen_inputs_invalid_target_and_runtime_order_economics_fail_closed():
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_CUTOFF_AFTER_AS_OF"):
        frozen_input(information_cutoff=NOW + timedelta(seconds=1))
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_MANIFESTS_INVALID"):
        frozen_input(input_manifest_ids=("bad",))
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_TARGET_INVALID"):
        decision(target_weights={"SPY": Decimal(".8"), "CASH": Decimal(".1")})
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_ORDER_ECONOMICS_INVALID"):
        decision(risk_state=DecisionState.BLOCK)


def test_pricing_non_applicability_cannot_authorize_a_target():
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_PRICING_REQUIRED_FOR_AUTHORIZED_TARGET"):
        decision(
            pricing_outputs={},
            pricing_applicable=False,
            pricing_non_applicability_reason="asset-class-has-no-approved-pricing-model",
            pricing_applicability_evidence_id=NON_APPLICABLE_EVIDENCE.evidence_id,
        )
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_PRICING_APPLICABILITY_INVALID"):
        decision(pricing_outputs={}, pricing_applicable=False)
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_PRICING_APPLICABILITY_INVALID"):
        decision(pricing_applicable=False, pricing_non_applicability_reason="not-applicable")
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_OUTPUT_INVALID"):
        decision(pricing_outputs={})
    with pytest.raises(InvariantViolation, match="DECISION_KERNEL_PRICING_APPLICABILITY_INVALID"):
        decision(pricing_non_applicability_reason="not-applicable")


def test_schema_covers_published_parity_evidence():
    inputs = frozen_input()
    ledger = DecisionParityLedger()
    result = ledger.record(DecisionRuntimeAdapter(
        DecisionKernel("decision-kernel@1", lambda _: decision()),
        adapter(DecisionRuntime.HISTORICAL_REPLAY),
    ).decide(inputs))
    schema = json.loads((Path(__file__).parents[1] / "schemas/decision_kernel_parity.schema.json").read_text())
    payload = ledger.payload()
    assert set(schema["required"]) == set(payload)
    assert set(schema["$defs"]["adapter"]["required"]) == set(result.adapter.payload())
    assert set(schema["$defs"]["decision"]["required"]) == set(result.decision.payload())
    assert set(schema["$defs"]["evaluation"]["required"]) == set(payload["evaluations"][0])
