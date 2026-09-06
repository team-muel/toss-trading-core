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
    "BacktestPeriod", "BacktestRunEvent", "BacktestRunRegistry", "BacktestRunSpec",
    "BacktestRunStatus",
]
