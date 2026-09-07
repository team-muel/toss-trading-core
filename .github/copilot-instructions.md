# Copilot review instructions

Treat this repository as high-risk financial infrastructure even while live trading remains disabled.

When reviewing a pull request, prioritize:

1. correctness and invariant preservation,
2. fail-closed behavior under stale, conflicting, incomplete, or malformed evidence,
3. authorization and mode boundaries between research, paper, shadow, and live paths,
4. point-in-time semantics and prevention of future-data leakage,
5. idempotency, replay parity, recovery, and reconciliation,
6. secret handling and sensitive account data,
7. negative-path tests and regression coverage.

Do not approve a change merely because tests pass. Check whether tests encode the intended contract and whether critical failure paths are covered.

When the PR references a Linear issue key such as `AMA-123`, use that context when available to verify scope and acceptance criteria. Flag scope drift explicitly.

For changes under `src/toss_trading/execution/`, `src/toss_trading/broker/`, `src/toss_trading/risk/`, schemas, or governance scripts, use a higher review bar and call out any authority boundary that becomes less explicit.

Material correctness, security, data-integrity, or governance findings should be written as actionable review findings, not optional style suggestions.
