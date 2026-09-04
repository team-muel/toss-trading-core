# Risk policy

- Version: `risk-v0-draft`
- Status: `DRAFT / NOT APPROVED`

Limits cover total drawdown, position/event loss, daily and weekly warnings,
minimum cash, maximum equity/single-name/sector/currency exposure, turnover,
liquidity, and stress loss. Values must be derived from loss the user can bear,
never selected because a backtest performs best.

No numeric limit is authorized yet. A null or absent limit is not infinity; it is
`POLICY_MISSING` and results in `BLOCK/NO_TRADE`. Any stale, conflicting, missing,
or unreconciled input also blocks. Risk measurement cannot authorize a trade;
only the versioned Risk Governor may return ALLOW, REDUCE, or BLOCK.
