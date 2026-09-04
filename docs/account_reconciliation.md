# Account reconciliation

Version: `account-reconciliation-v1`

Before financial calculation or a new order intent, the immutable Toss account-truth
snapshot is compared with the event-derived internal ledger. The required targets are
holdings and average cost, cash, open-order membership, order state, cumulative fill
quantity and amount, commission, tax, settlement date, sellable quantity, and buying
power. Missing either side is `UNVERIFIABLE`; it is never interpreted as zero or an
empty account.

Every numeric comparison uses an approved, effective tolerance policy. Rules may be
general or scoped to a currency and/or instrument. The most specific rule wins. A
missing rule is `UNVERIFIABLE`. Categorical values, order membership, state, and
settlement dates require exact equality. Results are `MATCH`, `TOLERANCE_MATCH`,
`MISMATCH`, `UNVERIFIABLE`, or `BLOCKED`.

Toss buying power is treated as a broker constraint, not as cash. The approved Toss
account response currently does not guarantee a directly reconcilable cash-balance
field. Consequently, `CASH` remains `UNVERIFIABLE` unless the captured account-truth
contract contains explicit per-currency cash balances. This limitation is surfaced as
`NO_NEW_TRADES`; buying power is still reconciled separately.

Each mismatch or unverifiable item creates an append-only issue with its detection
time, exact difference or missing side, and required action. A later matching run does
not remove the issue. Resolution requires a note, approver, timestamp, and the latest
reconciliation run containing `MATCH` or `TOLERANCE_MATCH` evidence for the same
target and dimension. Effective issue status is then `RESOLVED`; source records are
never updated.

The trade gate requires the named reconciliation run to be the latest for the account,
all current items to be eligible, and every historical issue to be explicitly resolved.
An order-intent authorization records that evidence. A database trigger rejects direct
`am_order_intent` insertion without a current eligible authorization, so bypassing the
application service cannot silently create a new order.

Backup/restore and replay preserve runs, items, issues, resolutions, tolerance policy,
and gate outcome. Raw-response and account-snapshot hashes are verified before every
new reconciliation run. Any missing, changed, stale, conflicting, or unreconciled
input fails closed.
