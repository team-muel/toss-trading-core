from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from asset_management.decisions import (
    DecisionState,
    ReasonCode,
    RegimeUncertaintyEvidence,
    RegimeUncertaintyPolicy,
    RiskGovernor,
    RiskGovernorPolicy,
    RiskInputs,
    bind_regime_uncertainty,
    evaluate_regime_uncertainty,
)
from asset_management.decisions.governor import SOFT_REDUCTIONS, target_weight_hash
from asset_management.domain.errors import InvariantViolation, NoTrade
from asset_management.states import (
    RegimeModelSpec,
    RegimeOutputSemantics,
    RegimeSnapshot,
    StateType,
)


NOW = datetime(2026, 9, 20, 7, tzinfo=timezone.utc)
TARGET = {"SPY": Decimal("0.8"), "CASH": Decimal("0.2")}
TARGET_HASH = target_weight_hash(TARGET)


def ident(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()


def regime_snapshot(
    *,
    semantics=RegimeOutputSemantics.FILTERED_CAUSAL,
    confidence=Decimal("0.45"),
    probabilities=None,
    entropy=Decimal("1.05"),
):
    spec = RegimeModelSpec(
        model_id="market-regime",
        version="1",
        input_state_type=StateType.MARKET,
        input_component_ids=("growth", "credit", "volatility"),
        latent_state_ids=("state-0", "state-1", "state-2"),
        parameter_set_id="market-regime@1",
        purpose="probabilistic market regime evidence",
    )
    return RegimeSnapshot.create(
        source_state_id=ident("market-state"),
        spec=spec,
        as_of=NOW,
        information_cutoff=NOW - timedelta(minutes=1),
        output_semantics=semantics,
        state_probabilities=probabilities or {
            "state-0": Decimal("0.40"),
            "state-1": Decimal("0.35"),
            "state-2": Decimal("0.25"),
        },
        entropy=entropy,
        confidence=confidence,
        input_evidence_ids=(ident("state-evidence"),),
        code_revision="git:abcdef0",
    )


def uncertainty_policy():
    return RegimeUncertaintyPolicy(
        policy_version="regime-uncertainty@1",
        minimum_confidence=Decimal("0.60"),
        minimum_dominant_probability=Decimal("0.55"),
        maximum_entropy=Decimal("0.90"),
    )


def risk_inputs():
    return RiskInputs(
        runtime_run_id="run-179",
        portfolio_target_id="target-179",
        portfolio_target_hash=TARGET_HASH,
        policy_version="risk@179",
        as_of_utc=(NOW + timedelta(minutes=1)).isoformat(),
        evidence_ids=(ident("account"), ident("target")),
    )


def governor():
    return RiskGovernor(RiskGovernorPolicy(
        "risk@179",
        {reason: Decimal("0.75") for _, reason in SOFT_REDUCTIONS},
    ))


def test_causal_regime_uncertainty_becomes_traceable_soft_risk_evidence():
    evidence = evaluate_regime_uncertainty(
        regime_snapshot(), uncertainty_policy(), evaluated_at=NOW
    )
    assert evidence.uncertain is True
    assert {reason.value for reason in evidence.reason_codes} == {
        "LOW_CONFIDENCE", "DIFFUSE_STATE_PROBABILITY", "HIGH_ENTROPY"
    }

    bound = bind_regime_uncertainty(risk_inputs(), evidence)
    assert bound.regime_uncertain is True
    assert bound.regime_uncertainty_evidence_id == evidence.evidence_id
    assert evidence.evidence_id in bound.evidence_ids

    decision = governor().decide(bound)
    assert decision.state is DecisionState.REDUCE
    assert ReasonCode.REGIME_UNCERTAIN in decision.reason_codes


def test_raw_regime_boolean_cannot_bypass_the_evidence_adapter():
    with pytest.raises(NoTrade, match="REGIME_UNCERTAINTY_EVIDENCE_REQUIRED"):
        replace(risk_inputs(), regime_uncertain=True)


def test_forged_regime_evidence_hash_cannot_bypass_the_typed_adapter():
    forged = ident("forged-regime-evidence")
    with pytest.raises(NoTrade, match="REGIME_UNCERTAINTY_EVIDENCE_ADAPTER_REQUIRED"):
        RiskInputs(
            runtime_run_id="run-179",
            portfolio_target_id="target-179",
            portfolio_target_hash=TARGET_HASH,
            policy_version="risk@179",
            as_of_utc=(NOW + timedelta(minutes=1)).isoformat(),
            evidence_ids=(ident("account"), forged),
            regime_uncertain=True,
            regime_uncertainty_evidence_id=forged,
        )


def test_retrospective_smoothed_regime_cannot_enter_risk():
    with pytest.raises(
        InvariantViolation, match="RETROSPECTIVE_REGIME_CANNOT_ENTER_RISK"
    ):
        evaluate_regime_uncertainty(
            regime_snapshot(semantics=RegimeOutputSemantics.SMOOTHED_RETROSPECTIVE),
            uncertainty_policy(),
            evaluated_at=NOW,
        )


def test_future_regime_uncertainty_evidence_cannot_bind_to_earlier_risk_run():
    evidence = evaluate_regime_uncertainty(
        regime_snapshot(), uncertainty_policy(), evaluated_at=NOW + timedelta(minutes=2)
    )
    with pytest.raises(InvariantViolation, match="FUTURE_REGIME_EVIDENCE_FORBIDDEN"):
        bind_regime_uncertainty(risk_inputs(), evidence)


def test_confident_regime_evidence_is_traceable_without_forcing_reduction():
    evidence = evaluate_regime_uncertainty(
        regime_snapshot(
            confidence=Decimal("0.90"),
            probabilities={
                "state-0": Decimal("0.80"),
                "state-1": Decimal("0.15"),
                "state-2": Decimal("0.05"),
            },
            entropy=Decimal("0.50"),
        ),
        uncertainty_policy(),
        evaluated_at=NOW,
    )
    assert evidence.uncertain is False
    assert evidence.reason_codes == ()

    bound = bind_regime_uncertainty(risk_inputs(), evidence)
    assert bound.regime_uncertain is False
    assert bound.regime_uncertainty_evidence_id == evidence.evidence_id
    assert governor().decide(bound).state is DecisionState.ALLOW


def test_regime_uncertainty_evidence_identity_cannot_be_forged():
    evidence = evaluate_regime_uncertainty(
        regime_snapshot(), uncertainty_policy(), evaluated_at=NOW
    )
    with pytest.raises(
        InvariantViolation, match="REGIME_UNCERTAINTY_EVIDENCE_IDENTITY_MISMATCH"
    ):
        replace(evidence, evidence_id=ident("forged"))


def test_regime_evidence_contract_has_no_target_or_order_authority():
    evidence = evaluate_regime_uncertainty(
        regime_snapshot(), uncertainty_policy(), evaluated_at=NOW
    )
    encoded = str(evidence.payload()).upper()
    assert "TARGET_WEIGHT" not in encoded
    assert "ORDER_INTENT" not in encoded
    assert "BUY" not in encoded
    assert "SELL" not in encoded
