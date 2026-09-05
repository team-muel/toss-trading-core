# Risk policy

- Version: `risk-v0-draft`
- Status: `DRAFT / NOT APPROVED`

Limits cover total drawdown, position/event loss, daily and weekly warnings,
minimum cash, maximum equity/single-name/sector/currency exposure, turnover,
liquidity, and stress loss. Values must be derived from loss the user can bear,
never selected because a backtest performs best.

No numeric limit is authorized yet. A null or absent limit is not infinity; it is
`POLICY_MISSING` and results in `BLOCK/NO_TRADE`. Any stale, conflicting, missing,
or unreconciled input also blocks. Risk measurement cannot authorize a trade.
Only the versioned Risk Governor may return `ALLOW`, `REDUCE`, `BLOCK`,
`ABSTAIN`, or `DEFER`. Only `ALLOW` and `REDUCE` authorize the exact runtime and
portfolio-target hash that was reviewed.

The following conditions are unconditional hard blocks: failed reconciliation,
unknown order state, unconfirmed initial cash, missing same-run account snapshot,
clock risk, stale execution price, data conflict, failed risk model, infeasible
optimizer, policy mismatch, duplicate order intent, active kill switch, and an
unauthorized runtime mode.

An approved policy must explicitly define a multiplier strictly between zero and
one for each soft condition: high volatility, low confidence, event risk,
sector/factor concentration, high spread/turnover, regime uncertainty, and risk
estimate uncertainty. Missing multipliers block policy construction. Concurrent
soft conditions use the smallest cap. Every block, abstention, deferral, and
reduction records stable reason codes.
