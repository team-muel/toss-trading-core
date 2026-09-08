"""Signal contracts and point-in-time evaluation."""

from .models import (
    CostSensitivity, SignalContext, SignalDefinition, SignalDirectionality,
    SignalFeatureInput, SignalSnapshot, SignalType,
)
from .registry import SignalRegistry
from .store import SignalRunResult, SignalStore
from .diagnostics import (
    CrossSectionalObservation, DiagnosticConfig, DiagnosticReport, DiagnosticRunResult,
    SignalDiagnosticsStore, TimeSeriesObservation,
)
from .neutralization import (
    NeutralizationConfig, NeutralizationInput, NeutralizationResult, SignalNeutralizer,
)
from .forecast_calibration import (
    CalibrationSample, ForecastCalibrationConfig, ForecastCalibrationRequest,
    ForecastCalibrationResult, SignalForecastCalibrator,
)
from .forecast_combination import (
    ForecastCombinationParameters, ForecastCombinationRegistry, ForecastCombinationRequest,
    ForecastCombinationResult, ForecastCombiner, ForecastSource,
)
from .research_bridge import (
    CostTiming, DecayStage, GrossNetBasis, ResearchSignalBridgeContract,
    ResearchSignalBridgeRecord, bridge_history_result, require_decay_stage_available,
)

__all__ = [
    "CostSensitivity", "SignalContext", "SignalDefinition", "SignalDirectionality",
    "SignalFeatureInput", "SignalRegistry", "SignalRunResult", "SignalSnapshot",
    "SignalStore", "SignalType", "CrossSectionalObservation", "DiagnosticConfig",
    "DiagnosticReport", "DiagnosticRunResult", "SignalDiagnosticsStore", "TimeSeriesObservation",
    "NeutralizationConfig", "NeutralizationInput", "NeutralizationResult", "SignalNeutralizer",
    "CalibrationSample", "ForecastCalibrationConfig", "ForecastCalibrationRequest",
    "ForecastCalibrationResult", "SignalForecastCalibrator",
    "ForecastCombinationParameters", "ForecastCombinationRegistry", "ForecastCombinationRequest",
    "ForecastCombinationResult", "ForecastCombiner", "ForecastSource",
    "CostTiming", "DecayStage", "GrossNetBasis", "ResearchSignalBridgeContract",
    "ResearchSignalBridgeRecord", "bridge_history_result", "require_decay_stage_available",
]
