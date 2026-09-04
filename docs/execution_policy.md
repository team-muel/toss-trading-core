# Execution policy

- Version: `execution-v0-draft`
- Status: `DRAFT / NOT APPROVED`
- Live trading: disabled.
- Progression: Read-only -> Replay -> Paper -> Shadow -> Semi-auto -> Micro-live.
- A strategy outputs target weights, never a broker order.
- Execution consumes an approved Order Intent and cannot recompute features,
  expected returns, target weights, or risk decisions.
- Quantity, price, commission, tax, cash, and settlement use exact decimal values.
- Writes require durable idempotency keys. Duplicate delivery must not duplicate
  cash, positions, fills, or orders.
- Unknown broker status or incomplete reconciliation produces NO_TRADE.
- A new order intent requires the latest account reconciliation, no failed current
  item, no unresolved historical issue, and immutable reconciliation authorization.
  The database enforces this even when application code is bypassed.
