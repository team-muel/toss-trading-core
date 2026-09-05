# Alpha Management

`alpha_management` is the research-language companion to the modular `asset_management` core.

## Ownership boundary

- `asset_management` owns account truth, time truth, data truth, reference data, portfolio policy, risk control, and execution.
- `alpha_management` owns WorldQuant BRAIN-inspired alpha expression, operator vocabulary, research simulation, and evaluation.
- `alpha_management` may read validated repository values only through an explicit read-only datafield bridge.
- `alpha_management` must not call Toss or another provider directly, mutate account state, create an order, or bypass portfolio/risk policy.

This preserves the modular-monolith runtime order while restoring the useful intent of the earlier alpha-expression work.

## Data flow

```text
provider / Toss / macro source
        |
        v
asset_management.data
  raw -> bronze -> silver -> gold
        |
        +-- point-in-time / reference / quality validation
        |
        v
alpha_management.datafields
        |
        v
BRAIN-style operators and expressions
        |
        v
research-only simulated weights
        |
        v
metrics / IS-OS validation / fitness
```

The handoff back into portfolio decisions is intentionally not implemented in this first slice. A later integration layer must convert validated alpha research output into governed financial-calculation or target-portfolio evidence. That conversion remains subject to the `asset_management` policy, account-truth, time-truth, data-truth, risk-control, and order gates.

## Single-owner map

| Concern | Owner | Compatibility boundary |
| --- | --- | --- |
| Alpha expressions, operators, simulation, metrics | `alpha_management` | `toss_trading.alpha` re-exports the canonical objects |
| Validated point-in-time alpha inputs | `asset_management.data/time/reference` | `alpha_management.PointInTimeDataSource` is read-only |
| Operational research collection, backtests, reports | `toss_trading.research` | May consume canonical alpha outputs; it does not redefine alpha operators |
| Legacy runtime signal proposal | `toss_trading.alpha.expression.to_signals` | Outer adapter only; proposals still pass risk and execution gates |
| Account, ledger, portfolio, risk, execution truth | `asset_management` | Legacy runtime migration adapters must not become new domain owners |

`toss_trading.alpha.operators` and `toss_trading.alpha.metrics` contain no
implementation. They are compatibility modules so existing imports continue to
work while all behavior comes from `alpha_management`. Provider-specific legacy
datafields remain isolated under `toss_trading.alpha.datafields`; they are not a
second data-truth boundary and must be normalized through `asset_management`
before new alpha-management research consumes them.

## Initial operator vocabulary

Cross-sectional: `rank`, `zscore`, `scale`, `sign`, `winsorize`, `group_neutralize`, `group_rank`, `truncate`.

Time-series: `ts_delay`, `ts_delta`, `ts_sum`, `ts_mean`, `ts_stddev`, `ts_zscore`, `ts_rank`, `ts_decay_linear`, `ts_max`, `ts_min`.

## Removed from the old design

Naver Search HUB is not part of this module. Provider-specific HTTP access is not part of the alpha language at all. Datafields are repository-facing read vocabulary, not data collectors.
