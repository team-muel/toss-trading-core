# AMA-101 — Toss accounting source boundary

The official [REST document](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)
was rechecked on 2026-09-08 KST: version 1.2.14, SHA-256
`a7b32ba754401d13fa649ba91eebd212420eb1afab28e9c2c0d6ea8d43055fed`, matching the existing
approved repository contract. No new broker capability is approved by this check.

Account identifies accounts. HoldingsOverview describes stock holdings and their valuation;
it is not a complete accounting NAV statement. BuyingPowerResponse supplies currency and
cashBuyingPower, a cash-based order constraint. These reviewed contracts do not establish
settled accounting cash, receivable/payable inclusion or other net assets. Neither the stock
valuation nor cashBuyingPower can fill those gaps. Completing the NAV bridge requires a
separately verified accounting source/statement and its inclusion contract.

`cash_state_from_buying_power` connects the information actually available to the existing
CashLedger. It reads a hash-verified raw response, requires Toss GET /api/v1/buying-power,
HTTP 200, the expected account, contract version and currency, and checks finite nonnegative
Decimal buying power. Future responses and responses older than the explicit max_age policy
are rejected. Freshness starts at request time; delayed receipt cannot refresh an old request.

An independently evidenced cash opening and existing ledger events/reservations remain
mandatory. The function never opens a cash balance or posts an asset from buying power.
`internal_free_cash = settled_cash - reserved_cash - operational_liquidity_reserve`;
`cash_constrained_buy_limit = min(internal_free_cash, broker_buying_power)`. Unsettled
proceeds are not spendable. A reserve larger than available cash fails closed.

The cash-constraint-evidence@1 result binds amount, currency, policy, timestamps and raw
request/response hashes. This is a cash ceiling, not permission to trade: account reconciliation,
session, capability, risk and order checks still apply. The raw-store hash protects stored body
integrity; it is not a provider signature or proof of semantic truth for unprovided fields.
The existing unscoped BrokerConstraint/legacy state interface is preserved for old replay;
new provider-backed calculations should use the verified entry point.

The actual Toss collector also checks returned buying-power currency against the requested
currency and rejects negative values. Thus an incompatible response cannot enter a successful
new account-truth collection. No real orders or live-trading setting are changed.
