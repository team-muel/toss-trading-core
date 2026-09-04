# ADR 0001: Source of truth

Status: Accepted. Toss responses plus the internal append-only ledger are authoritative
for accounts, orders, fills, fees, sellable quantity, and buying power. External prices
cannot overwrite account truth. A disagreement is UNRECONCILED and blocks orders.
