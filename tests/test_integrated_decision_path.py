from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import yaml

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.decisions.governor import (
    DecisionState, RiskGovernor, RiskGovernorPolicy, RiskInputs, SOFT_REDUCTIONS,
    target_weight_hash,
)
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.features.models import FeatureSnapshot
from asset_management.orchestration import (
    CanonicalDecisionRequest, DecisionParityLedger, DecisionRuntime, FrozenDecisionInput,
    PricingApplicabilityEvidence, RuntimeAdapterDescriptor,
)
from asset_management.orchestration.runtime import ApplicationRuntime
from asset_management.portfolio.allocator import select_securities
from asset_management.quality.models import QualityStatus
from asset_management.signals import (
    ForecastCombinationParameters, ForecastCombinationRegistry, ForecastCombinationRequest,
    ForecastCombiner, ForecastSource, SignalSnapshot,
)
from asset_management.time.clock import FrozenClock


NOW = datetime(2026, 9, 8, 1, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(minutes=1)
VALID_UNTIL = NOW + timedelta(days=21)
MANIFEST = "a" * 64
FEATURE_MANIFEST = "b" * 64
HISTORY_MANIFEST = "c" * 64
PRICING_EVIDENCE = PricingApplicabilityEvidence.create(
    scope_key="integrated-quality-momentum@1",
    applicable=True,
    reason=None,
    policy_version="pricing-applicability@1",
)


def adapter(runtime):
    return RuntimeAdapterDescriptor(
        runtime=runtime,
        clock_adapter_key=f"clock-{runtime.value}@1",
        data_source_adapter_key=f"data-{runtime.value}@1",
        broker_adapter_key=f"broker-{runtime.value}@1",
        execution_adapter_key=f"execution-{runtime.value}@1",
        persistence_adapter_key=f"persistence-{runtime.value}@1",
    )


def source(*, signal_id, point_estimates, calibration, neutralization, confidence):
    return ForecastSource(
        forecast_calibration_id=calibration,
        signal_run_id=digest(canonical({"signal": signal_id})),
        neutralization_id=neutralization,
        signal_id=signal_id,
        as_of=NOW,
        information_cutoff=CUTOFF,
        oos_evidence_available_at=CUTOFF - timedelta(minutes=1),
        universe_manifest_id=MANIFEST,
        currency="USD",
        unit="DECIMAL_RETURN",
        validity=SignalValidity(21, 21, VALID_UNTIL, DecayProfile.STEP),
        point_estimates=point_estimates,
        uncertainty=Decimal(".01"),
        confidence=confidence,
        incremental_ic=Decimal(".05"),
        stability=Decimal(".8"),
        coverage=Decimal("1"),
        regime_sensitivity=Decimal(".1"),
        turnover=Decimal(".1"),
        implementation_cost=Decimal(".001"),
    )


def test_real_modules_form_one_deterministic_replay_and_paper_decision_path(tmp_path):
    validity = SignalValidity(21, 21, VALID_UNTIL, DecayProfile.STEP)
    feature = FeatureSnapshot(
        feature_run_id="feature-run@1", instrument_id="SPY", feature_id="quality.score",
        as_of=NOW.isoformat(), information_cutoff=CUTOFF.isoformat(), value="0.7",
        quality_status=QualityStatus.VALID.value, input_manifest_ids=(MANIFEST,),
        parameter_set_id="feature-params@1", parent_state_id=None, code_revision="git:abcdef1",
        validity=validity,
    )
    signal_values = {"SPY": "0.4", "QQQ": "0.2"}
    signal = SignalSnapshot(
        signal_run_id=digest(canonical({"signal": signal_values, "as_of": NOW.isoformat()})),
        signal_id="quality.signal", signal_version="1", semantic_type="SIGNAL_VALUE",
        as_of=NOW.isoformat(), information_cutoff=CUTOFF.isoformat(), values=signal_values,
        quality_status=QualityStatus.VALID.value, coverage="1",
        source_feature_manifest_ids=(FEATURE_MANIFEST,), history_feature_manifest_ids=(HISTORY_MANIFEST,),
        universe_manifest_id=MANIFEST, formula_version="quality-signal@1",
        parameter_set_id="signal-params@1", code_revision="git:abcdef1", validity=validity,
        output_hash=digest(canonical(signal_values)),
    )

    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    parameters = ForecastCombinationParameters(
        combination_id="combined-signal", version="1", max_forecast_weight=Decimal(".8"),
        cost_penalty=Decimal("1"), formula_version="forecast-combination@1",
        parameter_set_id="forecast-combination-params@1",
    )
    combiner = ForecastCombiner(store, ForecastCombinationRegistry((parameters,)))
    request = ForecastCombinationRequest(
        sources=(
            source(signal_id="quality.signal", point_estimates={"SPY": Decimal(".08"), "QQQ": Decimal(".05")}, calibration="d" * 64, neutralization="e" * 64, confidence=Decimal(".8")),
            source(signal_id="momentum.signal", point_estimates={"SPY": Decimal(".04"), "QQQ": Decimal(".09")}, calibration="f" * 64, neutralization="1" * 64, confidence=Decimal(".7")),
        ),
        covariance=((Decimal(".0004"), Decimal(".00004")), (Decimal(".00004"), Decimal(".0004"))),
        correlation=((Decimal("1"), Decimal(".1")), (Decimal(".1"), Decimal("1"))),
        evaluated_at=NOW,
    )
    forecast = combiner.combine(request, combination_id="combined-signal", version="1")
    assert forecast.status == "READY" and forecast.report is not None
    combined_id = str(forecast.report["combined_forecast_id"])
    forecast_values = {instrument: Decimal(component["net_point_estimate"])
                       for instrument, component in forecast.report["components"].items()}

    risky = select_securities(Decimal(".8"), forecast_values)
    proposed = dict(zip(risky.instruments, risky.weights))
    proposed["CASH"] = Decimal("1") - sum(proposed.values(), Decimal("0"))
    target_hash = target_weight_hash(proposed)
    policy = RiskGovernorPolicy(policy_version="risk@1",
        reduction_multipliers={reason: Decimal(".8") for _, reason in SOFT_REDUCTIONS})
    governor = RiskGovernor(policy)
    risk = governor.decide(RiskInputs(
        runtime_run_id="integrated-run@1", portfolio_target_id="target@1",
        portfolio_target_hash=target_hash, policy_version="risk@1", as_of_utc=NOW.isoformat(),
        evidence_ids=(FEATURE_MANIFEST, signal.signal_run_id, combined_id),
    ))
    assert risk.state is DecisionState.ALLOW
    approved_weights = governor.apply_to_target(risk, proposed, cash_instrument_id="CASH")

    inputs = FrozenDecisionInput(
        snapshot_id="snapshot@integrated-1", strategy_key="integrated-quality-momentum@1",
        model_keys=("forecast-combination@1",),
        policy_versions={"risk": "risk@1", "investment": "investment@1",
                         "pricing_applicability": PRICING_EVIDENCE.policy_version},
        parameter_set_key="integrated-params@1",
        input_manifest_ids=(MANIFEST, FEATURE_MANIFEST, HISTORY_MANIFEST, PRICING_EVIDENCE.evidence_id),
        as_of=NOW, information_cutoff=CUTOFF, code_revision="git:abcdef1",
        pricing_applicability_evidence=PRICING_EVIDENCE,
    )

    decision_request = CanonicalDecisionRequest._from_persisted_pipeline(
        inputs=inputs, feature_values={feature.feature_id: Decimal(feature.value)},
        signal_values={key: Decimal(value) for key, value in signal.values.items()},
        forecast_values=forecast_values, pricing_outputs={"pricing-baseline": Decimal(".06")},
        pricing_applicable=True, pricing_non_applicability_reason=None,
        pricing_applicability_evidence_id=PRICING_EVIDENCE.evidence_id,
        risk_outputs={"exposure_multiplier": risk.exposure_multiplier}, target_weights=approved_weights,
        risk_decision_id=risk.risk_decision_id, risk_decision_hash=risk.content_hash,
        risk_state=risk.state, risk_reason_codes=tuple(reason.value for reason in risk.reason_codes),
        order_intent_economics={"objective": "rebalance-to-risk-approved-target@1"},
        data_lineage_ids=(MANIFEST, FEATURE_MANIFEST, HISTORY_MANIFEST),
        calculation_lineage_ids=(combined_id, risk.content_hash),
    )

    raw_config = yaml.safe_load((Path(__file__).parents[1] / "config/application.yaml").read_text(encoding="utf-8"))
    runtime = ApplicationRuntime.start(raw_config, FrozenClock(NOW))
    ledger = DecisionParityLedger()
    replay = ledger.record(runtime.decision_adapter(
        kernel_version="decision-kernel@1", descriptor=adapter(DecisionRuntime.HISTORICAL_REPLAY),
    ).decide(decision_request))
    paper = ledger.record(runtime.decision_adapter(
        kernel_version="decision-kernel@1", descriptor=adapter(DecisionRuntime.PAPER),
    ).decide(decision_request))
    assert replay.semantic_hash == paper.semantic_hash
    assert ledger.require_parity(inputs.input_hash, runtimes=(DecisionRuntime.HISTORICAL_REPLAY, DecisionRuntime.PAPER)) == replay.semantic_hash
    assert replay.decision.pricing_applicable is True
    assert replay.decision.pricing_outputs["pricing-baseline"] == Decimal(".06")
    assert sum(replay.decision.target_weights.values()) == Decimal("1")
