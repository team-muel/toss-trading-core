"""Out-of-sample validation and stage promotion."""

from .account_truth import (
    REQUIRED_CHECKS, AcceptanceDecision, AccountTruthGateInput, AccountTruthGateResult,
    CheckEvidence, evaluate_account_truth_gate,
)
from .temporal_truth import (
    REQUIRED_TEMPORAL_CHECKS, TemporalTruthGateInput, TemporalTruthGateResult,
    evaluate_temporal_truth_gate,
)
from .data_truth import (
    REQUIRED_DATA_CHECKS, DataTruthGateInput, DataTruthGateResult,
    evaluate_data_truth_gate,
)
from .feature_state_model_integrity import (
    REQUIRED_FEATURE_STATE_MODEL_CHECKS, FeatureStateModelIntegrityGateInput,
    FeatureStateModelIntegrityGateResult, evaluate_feature_state_model_integrity_gate,
)
from .signal_forecast_integrity import (
    REQUIRED_SIGNAL_FORECAST_CHECKS, SignalForecastIntegrityGateInput,
    SignalForecastIntegrityGateResult, evaluate_signal_forecast_integrity_gate,
)
from .portfolio_decision_integrity import (
    REQUIRED_PORTFOLIO_DECISION_INTEGRITY_CHECKS, PortfolioDecisionIntegrityGateInput,
    PortfolioDecisionIntegrityGateResult, evaluate_portfolio_decision_integrity_gate,
)
from .pricing_expectation_risk_integrity import (
    REQUIRED_PRICING_EXPECTATION_RISK_CHECKS, PricingExpectationRiskIntegrityGateInput,
    PricingExpectationRiskIntegrityGateResult, evaluate_pricing_expectation_risk_integrity_gate,
)
from .d2_runtime_evidence import (
    D2RuntimeEvidenceResult, FactorRiskRuntimeEvidence, ModelLineageRuntimeEvidence,
    RiskFreeRuntimeEvidence, RUNTIME_D2_CHECKS, assemble_d2_runtime_evidence, build_d2_gate_input,
)
from .backtest_run_specification import (
    BacktestPeriod, BacktestRunEvent, BacktestRunRegistry, BacktestRunSpec, BacktestRunStatus,
)

__all__ = [
    "REQUIRED_CHECKS", "AcceptanceDecision", "AccountTruthGateInput",
    "AccountTruthGateResult", "CheckEvidence", "evaluate_account_truth_gate",
    "REQUIRED_TEMPORAL_CHECKS", "TemporalTruthGateInput", "TemporalTruthGateResult",
    "evaluate_temporal_truth_gate",
    "REQUIRED_DATA_CHECKS", "DataTruthGateInput", "DataTruthGateResult",
    "evaluate_data_truth_gate",
    "REQUIRED_FEATURE_STATE_MODEL_CHECKS", "FeatureStateModelIntegrityGateInput",
    "FeatureStateModelIntegrityGateResult", "evaluate_feature_state_model_integrity_gate",
    "REQUIRED_SIGNAL_FORECAST_CHECKS", "SignalForecastIntegrityGateInput",
    "SignalForecastIntegrityGateResult", "evaluate_signal_forecast_integrity_gate",
    "REQUIRED_PORTFOLIO_DECISION_INTEGRITY_CHECKS", "PortfolioDecisionIntegrityGateInput",
    "PortfolioDecisionIntegrityGateResult", "evaluate_portfolio_decision_integrity_gate",
    "REQUIRED_PRICING_EXPECTATION_RISK_CHECKS", "PricingExpectationRiskIntegrityGateInput",
    "PricingExpectationRiskIntegrityGateResult", "evaluate_pricing_expectation_risk_integrity_gate",
    "D2RuntimeEvidenceResult", "FactorRiskRuntimeEvidence", "ModelLineageRuntimeEvidence",
    "RiskFreeRuntimeEvidence", "RUNTIME_D2_CHECKS", "assemble_d2_runtime_evidence", "build_d2_gate_input",
    "BacktestPeriod", "BacktestRunEvent", "BacktestRunRegistry", "BacktestRunSpec",
    "BacktestRunStatus",
]
