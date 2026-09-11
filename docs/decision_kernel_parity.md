# AMA-123 — Research–Production Parity and One Decision Kernel

`DecisionKernel.evaluate` accepts only a verified `PipelineEvidenceRepository`, runtime run ID,
pricing-applicability authority and adapter descriptor. It assembles the `CanonicalDecisionRequest`
itself, so a caller cannot pass economic values or substitute a different calculation for a runtime
adapter. The internal pure evaluator exists only for focused parity unit tests.

`ApplicationRuntime.decision_adapter` is the composition-root entry point for the historical-replay,
paper, shadow, or live I/O descriptor. It requires the verified `PipelineEvidenceRepository`, runtime
run ID and pricing-applicability authority; the public adapter accepts no decision request or economic
values from its caller. Adapter keys are recorded with each evaluation but excluded from the semantic
hash. The hash instead binds the kernel version, frozen input hash, complete pre-execution decision,
and data/calculation lineage.

Production requests are assembled by `PipelineEvidenceRepository.assemble_canonical_decision_request`.
It re-resolves the contiguous persisted stages through `RISK_CONTROL`, verifies the selected JSON
artifact hashes, and reads feature, signal, pricing, forecast, risk, target, risk-decision and
order-economics values from those artifacts only. Economic artifact rows are append-only, and the
risk-decision hash is recomputed from the immutable target, approved risk policy, action and reason
codes before evaluation. The kernel also rejects any post-assembly economic-value mutation.

`DecisionParityLedger` accepts an evaluation only when every runtime for the same input has the same
semantic hash. It rejects divergent or overwritten runtime evidence and can require the full
historical-replay/paper/shadow/live set before accepting parity. This is pre-execution governance
only: the live adapter does not transmit orders or enable live trading.
