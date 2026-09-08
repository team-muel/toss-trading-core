# Gate D2 — Pricing, Expectation and Risk Integrity

Gate D2 requires the exact acceptance checks from AMA-50. A missing, unknown, or failed
check returns `FAIL`, records a stable `CHECK_FAILED:<name>` reason, and keeps
`permits_m5_execution` false. A PASS is possible only when every immutable evidence reference
in the exact check set is present and marked passed.

The current recorded result is intentionally `FAIL`: common risk-free horizon/currency/compounding
runtime evidence, factor specific-risk floor evidence, and complete model-scope-to-calculation
lineage evidence have not yet been assembled on a common production path. Fixture tests and
individual module contracts do not replace those inputs. The gate never creates orders and does
not change `live_trading_enabled=false`.
