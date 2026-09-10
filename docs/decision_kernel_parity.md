# AMA-123 — Research–Production Parity and One Decision Kernel

`DecisionKernel` accepts a `CanonicalDecisionRequest`: a frozen input snapshot plus the typed,
authoritative feature, signal, forecast, pricing, risk, target, risk-decision, order-economics and
lineage outputs assembled by the canonical pipeline. It has no public calculation callback, so a
caller cannot substitute a different calculation for a runtime adapter.

`ApplicationRuntime.decision_adapter` is the composition-root entry point for the historical-replay,
paper, shadow, or live I/O descriptor and then calls the same kernel. Adapter keys are recorded with
each evaluation but excluded from the semantic hash. The hash instead binds the kernel version,
frozen input hash, complete pre-execution decision, and data/calculation lineage.

`DecisionParityLedger` accepts an evaluation only when every runtime for the same input has the same
semantic hash. It rejects divergent or overwritten runtime evidence and can require the full
historical-replay/paper/shadow/live set before accepting parity. This is pre-execution governance
only: the live adapter does not transmit orders or enable live trading.
