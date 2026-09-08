"""In-process pipeline orchestration for the modular monolith."""

from .decision_kernel import (
    DecisionKernel, DecisionKernelEvaluation, DecisionParityLedger, DecisionRuntime,
    DecisionRuntimeAdapter, FrozenDecisionInput, PreExecutionDecision, RuntimeAdapterDescriptor,
)
from .research_bridge import (
    CostTiming, DecayStage, GrossNetBasis, ResearchSignalBridgeContract,
    ResearchSignalBridgeRecord, bridge_history_result, require_decay_stage_available,
)

__all__ = [
    "DecisionKernel", "DecisionKernelEvaluation", "DecisionParityLedger",
    "DecisionRuntime", "DecisionRuntimeAdapter", "FrozenDecisionInput",
    "PreExecutionDecision", "RuntimeAdapterDescriptor",
    "CostTiming", "DecayStage", "GrossNetBasis", "ResearchSignalBridgeContract",
    "ResearchSignalBridgeRecord", "bridge_history_result", "require_decay_stage_available",
]
