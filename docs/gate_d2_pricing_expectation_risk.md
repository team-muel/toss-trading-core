# Gate D2 — Pricing, Expectation and Risk Integrity

Gate D2 requires the exact acceptance checks from AMA-50. A missing, unknown, or failed
check returns `FAIL`, records a stable `CHECK_FAILED:<name>` reason, and keeps
`permits_m5_execution` false. A PASS is possible only when every immutable evidence reference
in the exact check set is present and marked passed.

The current recorded result is `PASS`: the typed risk-free curve preserves its
currency/horizon/compounding context, the factor-risk assessment applies and records a positive
specific-risk floor, and the model-calculation binding joins an active authorization to the final
immutable calculation lineage. The gate never creates orders and does not change
`live_trading_enabled=false`.
