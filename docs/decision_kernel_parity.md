# AMA-123 — Research–Production Parity and One Decision Kernel

`DecisionKernel` accepts a `CanonicalDecisionRequest`: a frozen input snapshot plus the typed,
authoritative feature, signal, forecast, pricing, risk, target, risk-decision, order-economics and
lineage outputs assembled by the canonical pipeline. It has no public calculation callback, so a
caller cannot substitute a different calculation for a runtime adapter.

`ApplicationRuntime.decision_adapter` is the composition-root entry point for the historical-replay,
paper, shadow, or live I/O descriptor. It requires the verified `PipelineEvidenceRepository`, runtime
run ID and pricing-applicability authority; the public adapter accepts no decision request or economic
values from its caller. Adapter keys are recorded with each evaluation but excluded from the semantic
hash. The hash instead binds the kernel version, frozen input hash, complete pre-execution decision,
and data/calculation lineage.

Production requests are assembled by `PipelineEvidenceRepository.assemble_canonical_decision_request`.
It re-resolves the contiguous persisted stages through `RISK_CONTROL`, verifies the selected JSON
artifact hashes, and reads feature, signal, pricing, forecast, risk, target, risk-decision and
order-economics values from those artifacts only. The public request constructor is not sufficient
to evaluate a decision: the kernel requires the assembler capability and rejects any post-assembly
economic-value mutation.

`DecisionParityLedger` accepts an evaluation only when every runtime for the same input has the same
semantic hash. It rejects divergent or overwritten runtime evidence and can require the full
historical-replay/paper/shadow/live set before accepting parity. This is pre-execution governance
only: the live adapter does not transmit orders or enable live trading.
