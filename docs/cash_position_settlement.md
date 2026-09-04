# Cash, position, settlement, and tax-lot ledger

Version: `cash-position-settlement-v1`

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

For a currency, settled and unsettled cash are separated by settlement date.
Available cash is settled cash less the latest reservation for each open buy order.
Orderable cash is the lesser of available cash and the broker buying-power
constraint. Quantity orders reserve remaining quantity times limit price plus
expected fees; amount orders reserve remaining amount plus fees. If neither price
nor amount is known, buying is blocked.

Positions retain total, settled and unsettled quantity, latest open-sell reservation,
broker sellable constraint, native currency, and remaining-lot weighted average cost.
A reservation above settled quantity is unreconciled and blocks selling.

Tax lots preserve acquisition and settlement dates, quantity, price, commission,
currency, FX rate, remaining quantity through append-only disposal events, and an
explicit tax-policy version. Disposal uses the requested versioned FIFO policy; no
tax policy is hidden as a permanent code default.

Replay reads only immutable openings, cash/position events, settlement records,
reservations, tax lots, and disposals. Replaying the same execution delta or broker
reservation cannot duplicate cash, quantity, fees, tax, or reserved balances.
