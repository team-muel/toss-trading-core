# Order intent and session planner

The M6 planner converts an `OrderIntent` that is already bound to an approved
`RiskDecision` into deterministic planning evidence. It never calls a broker
endpoint, submits an order, or changes `live_trading_enabled`.

For each target instrument, it derives the target quantity from NAV and the
executable quote, rounds it down to the instrument lot size, then nets the
signed current position and every OPEN or partial-fill exposure. `OpenOrderExposure`
uses `ordered_quantity - cumulative_filled_quantity`, so quantities already
reflected in the account position are never counted twice. Crossed active orders
for one instrument and unknown order states fail closed. A positive remaining
delta becomes `BUY`; a negative delta becomes `SELL`. A fully netted or
sub-minimum delta yields no plan.

`quantity` is the tradable instrument quantity. `notional_amount` is the
quantity multiplied by the rounded limit price. They remain separate fields.
The limit price rounds down to the tick size and the expected fee rounds up to
the configured fee tick, so planned costs are conservative.

The planner fails closed when a target lacks a quote, rule, current quantity,
or valid numeric exposure; when the market session is closed; and when the
quote is stale or observed after the evaluation instant. Each plan binds the
risk decision, portfolio target hash, quote observation time, rounded values,
and side into a SHA-256 content hash. `order_intent_id` and `client_order_id`
derive from that hash, providing idempotency for equal planning inputs.

The JSON evidence contract is
[`schemas/order_intent_plan.schema.json`](../schemas/order_intent_plan.schema.json).
