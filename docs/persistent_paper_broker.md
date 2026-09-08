# Persistent Paper Broker — AMA-63

`PersistentPaperBroker(path, account='paper:...')` implements the core
`BrokerWritePort` submit/cancel methods and provides submit_order, cancel_order,
get_order_status, get_balances, get_positions and get_fills. It has no live
transport. The account must be explicitly initialized with currency, cash and
positions; missing accounts fail closed. Initialization retries cannot reset a
traded account. Each account supports one currency, long-only limit orders.
All instruments supplied to that account must be denominated in its currency.

Submit requires account, client_order_id, order_intent_id, instrument_id, side
(BUY/SELL), quantity, limit_price and expected_fee. Monetary and quantity fields
are finite decimal strings. Identical payload retries return the persisted
order; either identity reused with different payload raises
PAPER_IDEMPOTENCY_CONFLICT. Extra planner lineage fields are preserved and included
in the comparison. submit's idempotency_key must equal client_order_id; cancel's
key must also equal the client ID and its broker ID must match that order.

Open orders reserve buy notional and fee, or sell quantity and fee. Insufficient
unreserved cash/positions raises a DataQualityError (NO_TRADE). Cancellation
releases remaining reservations; filled/canceled orders stay terminal.
record_fill requires an explicit unique fill ID, quantity, price and fee.
It rejects overfills, limit-price violations, fee reserve overruns and fills on
closed orders. Identical fill retries are harmless; conflicting retries fail.
Partial fills, balance/position changes and fill records commit in one SQLite
transaction. BEGIN IMMEDIATE serializes account writers; synchronous FULL is
enabled. Stored decimal values never pass through binary floats.

The persistent contract is the paper_state table keyed by account, containing
a JSON body with initial account state, cash, currency, positions, orders and
fills. Each mutation replaces this body atomically. This is a small paper-core
store, not a production-volume event ledger. Database tamper detection and
cross-store crash recovery are not claimed here (AMA-116).

SubmissionJournal can call submit_order directly, preserving its durable-before-
transport and subsequent reconciliation requirement. A paper OPEN receipt alone
does not create reconciled broker evidence. This adapter never synthesizes fills
or market data: realistic slippage, session rules, FX and settlement remain
separate work. live_trading_enabled remains false.
