# ADR 0003: Money precision

Status: Accepted. Float is forbidden for money, quantity, price, commission, tax, and
settlement. Decimal strings cross storage boundaries. Market-specific increments and
rounding rules are explicit and versioned.
