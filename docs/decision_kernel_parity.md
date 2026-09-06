# AMA-123 — Research–Production Parity and One Decision Kernel

`DecisionKernel` accepts a frozen input snapshot and a single runtime-independent calculation
callable. The callable receives no runtime, broker, clock, execution, or persistence adapter, so it
cannot use an adapter-specific branch to alter feature, signal, forecast, pricing, risk, target, risk
decision, or pre-execution order economics.

`DecisionRuntimeAdapter` describes the historical-replay, paper, shadow, or live I/O boundary and
then calls the same kernel. Adapter keys are recorded with each evaluation but excluded from the
semantic hash. The hash instead binds the kernel version, frozen input hash, complete pre-execution
decision, and data/calculation lineage.

`DecisionParityLedger` accepts an evaluation only when every runtime for the same input has the same
semantic hash. It rejects divergent or overwritten runtime evidence and can require the full
historical-replay/paper/shadow/live set before accepting parity. This is pre-execution governance
only: the live adapter does not transmit orders or enable live trading.
