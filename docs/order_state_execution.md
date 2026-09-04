# Order state and execution ledger

Version: `order-state-execution-v1`

The internal order state machine represents `PLANNED`, `SUBMITTING`, `SUBMITTED`,
`ACKNOWLEDGED`, `OPEN`, `PARTIALLY_FILLED`, `FILLED`, `CANCEL_PENDING`, `CANCELED`,
`REPLACE_PENDING`, `REPLACED`, `REJECTED`, `UNKNOWN`, and `REVIEW_REQUIRED`.
Every observation is an append-only event linked to the broker order and, when it
originated at Toss, its immutable raw response.

Terminal states are `FILLED`, `CANCELED`, `REPLACED`, and `REJECTED`. A terminal
state cannot transition again. Unknown broker values are preserved as `UNKNOWN`
and block order activity. `CANCEL_REJECTED` and `REPLACE_REJECTED` become
`REVIEW_REQUIRED`; a fresh query of the original order must resolve them.

A submission timeout is not an order failure and never authorizes a blind retry.
Recovery first queries by the existing idempotency key. If broker acceptance cannot
be proven, the order remains `REVIEW_REQUIRED`. Resubmission is prohibited until a
terminal state is observed.

Toss execution values are cumulative. The execution ledger stores exact Decimal
strings for filled quantity, filled amount, average price, commission, and tax.
Only the difference from the preceding cumulative snapshot becomes a delta. An
identical cumulative snapshot is idempotent and creates no second delta; any decrease
is `UNRECONCILED` and blocks processing. Each delta has at most one posting link to
signed cash and position events, so replay cannot apply balances twice. Snapshot,
delta, posting, cash, and position tables are append-only.
