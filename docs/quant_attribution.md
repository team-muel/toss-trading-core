# AMA-71 — Quant Performance and Benchmark-relative Attribution

Quant attribution consumes only the mandate's effective primary benchmark version. It preserves
separate forecast total return, reference/constrained/executed expected active return, and realized
active return fields. Constraint drag, execution drag, and realization gap locate the loss of edge
without reusing a benchmark chosen after results are known. Ratios are omitted when their sign or
denominator makes them meaningless. P&L components remain separately attributable to the decision,
forecast, optimizer and execution policy lineage. This reporting path does not create orders.
