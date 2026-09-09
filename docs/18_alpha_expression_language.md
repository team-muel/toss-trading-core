# Alpha expression language

`alpha_management` owns the expression grammar, operators, simulation, and
research metrics. It is a sibling of `asset_management`, not a runtime layer.

## Boundary

- Alpha code may consume validated point-in-time inputs through read-only
  interfaces.
- It cannot call a provider or broker, mutate account state, create an order,
  or make a risk decision.
- A research result remains non-authoritative until an explicit outer bridge
  creates governed evidence for `asset_management`.
- That bridge cannot skip policy, account truth, time truth, data truth, risk,
  or order gates.

## Vocabulary

Cross-sectional operators include `rank`, `zscore`, `scale`, `sign`,
`winsorize`, `group_neutralize`, `group_rank`, and `truncate`.

Time-series operators include `ts_delay`, `ts_delta`, `ts_sum`, `ts_mean`,
`ts_stddev`, `ts_zscore`, `ts_rank`, `ts_decay_linear`, `ts_max`, and
`ts_min`.

The grammar resource is packaged with `alpha_management` and is verified by
the wheel smoke test. Collection and provider normalization remain in
`research_platform`; runtime decision authority remains solely in
`asset_management`.
