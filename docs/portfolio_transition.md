# AMA-58 — Portfolio Transition and Multi-period Rebalancing

`TransitionPlanner` turns a current `PortfolioTarget` and an already approved executable target
into a **weight-transition plan**. It does not construct an `OrderIntent`, a quantity, a limit
price, or a broker request. Order formation remains downstream of this planning boundary.

The planner persists the full immutable planning input and its SHA-256 input hash with the plan.
The input records the forecast values and `SignalValidity` (`valid_until` and decay / half-life),
the next rebalance date, cost-curve and tax-policy keys, per-instrument linear and market-impact
costs, per-step liquidity capacities, cash available, settlement availability, and upcoming event
times. `plan_hash` binds that evidence, the chosen mode, and every step.

## Transition economics and timing

For the same target displacement, the planner calculates the cost-after-forecast-decay expected
utility of an immediate transition and of equal staged slices. Transaction cost is
`linear_cost × |Δw| + impact_cost × Δw²`; tax cost is applied to each absolute weight change.
Staging can reduce convex impact and satisfy a smaller liquidity capacity, while later stages use
the decayed forecast weight. The selected path is `IMMEDIATE` or `STAGED` only when its utility is
positive and its cash, liquidity, timing, and settlement constraints are all satisfied.

Each non-cash sell step precedes any associated buy. A buy depends on the sell-step ID and records
`SELL_FILL_CONFIRMED` and `SETTLED_CASH_CONFIRMED`; cash-funded buys instead require
`AVAILABLE_CASH_CONFIRMED`. All steps require a clear event window, no open-order exposure, and an
open liquidity window. Every later stage, and every buy delayed for settlement, has
`requires_reassessment=true`. A scheduler must obtain a fresh plan from the latest account,
forecast, event, and settlement evidence before such a step can progress; it may not finish the
original target blindly.

The planner returns a step-free `DEFER` plan for an expired or fully decayed forecast, open-order
or partial-fill exposure, an upcoming event before the next rebalance, absent/late settlement,
cash or liquidity insufficiency, expired stage windows, or non-positive economic utility. These
stable reason codes keep missing and stale conditions fail-closed.

## Contract boundary

The serialized contract is [portfolio_transition.schema.json](../schemas/portfolio_transition.schema.json).
It separates `executable_target` from transition steps and leaves risk approval, order intent
creation, execution, fill handling, cash confirmation, and real-order submission to their
respective downstream controls. `live_trading_enabled` remains unchanged and false.
