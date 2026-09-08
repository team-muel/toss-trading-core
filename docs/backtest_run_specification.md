# AMA-119 — Backtest Run Specification and Preregistration

Every validation run is preregistered as a versioned, content-addressed specification before it
starts. The specification freezes the hypothesis, strategy, effective investor mandate and primary
benchmark, PIT universe and dataset manifests, information cutoff, chronological samples, horizons,
OOS objective, risk budgets and `lambda` policy range, constraints, acceptance thresholds, execution
assumptions, parameter-search budget, purge/embargo, robustness plan, random-seed policy, and code
revision.

The registry verifies the effective Mandate and Benchmark at the information cutoff. It requires the
run to copy their objective, reporting currency, rebalance conventions, strategic/active/tracking and
stress budgets, and both risk-aversion ranges. A non-mandated benchmark, expired authority, altered
budget, or substituted convention therefore fails closed.

`preregister` never overwrites a run key. A changed threshold, benchmark, mandate, risk budget,
`lambda` range, or search-space contract must have a new run version; its `parent_spec_hash` records
the predecessor when it is a continuation. `start` appends a start event, and `record_outcome` appends
exactly one terminal `COMPLETED`, `FAILED`, or `INTERRUPTED` event. All events carry the immutable
spec hash, evidence IDs, output hash, UTC timestamp, and explicit outcome reason. Failed and
interrupted runs are retained and published in the same content-addressed registry snapshot.

This validation contract is research governance only. It creates no target portfolio, risk approval,
or order and does not enable live trading.
