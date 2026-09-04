# Cash, position, settlement, and tax-lot ledger

Version: `cash-position-settlement-v2`

No strategy or portfolio calculation may consume the account until this ledger can
be reconstructed. Each account/currency opening cash balance requires an as-of time,
evidence, and approver. Missing evidence is `OPENING_BALANCE_UNKNOWN`, not zero.
Opening positions have the same evidence rule and create an explicit opening tax lot.

Cash events use exact Decimal strings and one of `DEPOSIT`, `WITHDRAWAL`,
`TRADE_COST`, `TRADE_PROCEEDS`, `COMMISSION`, `TAX`, `DIVIDEND`, `WITHHOLDING`,
`INTEREST`, `FX_CONVERSION_IN`, `FX_CONVERSION_OUT`, `CORPORATE_ACTION_CASH`, or
`MANUAL_ADJUSTMENT`. Manual adjustments require both a reason and approver. Trade
principal, commission, and tax are separate events in the instrument's native
currency; currencies are never implicitly aggregated or converted.
The database rejects a manual cash row unless its immutable authorization was
recorded first in the same transaction. Reusing an idempotency key is accepted only
when every economic field, timestamp, reason, and approver is identical.

For a currency, settled and unsettled cash are separated by settlement date.
Available cash is settled cash less the latest reservation for each open buy order.
Orderable cash is the lesser of available cash and the broker buying-power
constraint. Quantity orders reserve remaining quantity times limit price plus
expected fees; amount orders reserve remaining amount plus fees. If neither price
nor amount is known, buying is blocked.
Reservation updates preserve the original account/currency or account/instrument,
cannot move backwards in time, and release to exactly zero. Terminal order states
release any open reservation atomically. Broker buying-power and sellable constraints
require raw evidence plus an observed/valid-until interval and are rejected when
future, stale, or missing.

Positions retain total, settled and unsettled quantity, latest open-sell reservation,
broker sellable constraint, native currency, and remaining-lot weighted average cost.
A reservation above settled quantity is unreconciled and blocks selling.

Tax lots preserve acquisition and settlement dates, quantity, price, commission,
currency, FX rate, remaining quantity through append-only disposal events, and an
explicit tax-policy version. Disposal uses the requested versioned FIFO policy; no
tax policy is hidden as a permanent code default.
Lots and disposals carry separate immutable observation times, so an as-of query
cannot consume a future lot or disposal. Remaining-lot average cost allocates original
commission proportionally after partial disposal. Settlement cannot precede acquisition.

Replay reads only immutable openings, cash/position events, settlement records,
reservations, tax lots, and disposals. Replaying the same execution delta or broker
reservation cannot duplicate cash, quantity, fees, tax, or reserved balances.
Before rebuilding, replay verifies raw-response hashes, order-state and execution
sequence/chronology, cumulative-to-delta arithmetic, posting context hashes, exact
cash components, position effects, settlement links, reservation identity/release
rules, and the absence of unposted effectful deltas. Any inconsistency blocks output.
