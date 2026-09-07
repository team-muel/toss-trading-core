# Execution microstructure lite

This M6 layer evaluates a low-frequency arrival quote before it can become an
executable price reference. It deliberately excludes full depth-of-book and
queue-position modelling, and it does not submit orders.

`DecisionPrice` is the historical price used for the portfolio decision.
`ArrivalQuote` holds the bid, ask, mid, spread, timestamp, availability,
source, currency, exchange, session, auction, halt, suspension, volatility,
and spread conditions observed at execution time. The executable reference is
the ask for a buy and the bid for a sell, so it can never be silently replaced
by the decision price or a reference close.

The assessment checks the source-backed exchange session window and its
availability, validates the provider-declared session, detects crossed quotes,
and preserves all three price roles in the serialized evidence. Halt,
suspension, crossed quotes, price-reference conflicts, and calendar conflicts
return `BLOCK`. Stale or unavailable quotes, closed or disallowed extended
sessions, auctions, volatility spikes, and excessive or flagged spreads return
`DEFER`. Only a current compatible quote in a policy-permitted session can return `ALLOW`.
`to_executable_quote` is the sole bridge to the order-intent planner and rejects
every `DEFER` or `BLOCK` assessment. It passes the side-aware arrival reference,
never the decision price or a reference close.

The evidence schema is
[`schemas/execution_microstructure.schema.json`](../schemas/execution_microstructure.schema.json).
