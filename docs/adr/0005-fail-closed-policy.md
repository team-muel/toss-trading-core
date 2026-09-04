# ADR 0005: Fail-closed policy

Status: Accepted. UNKNOWN, MISSING, STALE, CONFLICT, UNRECONCILED, missing policy,
ambiguous ownership, and unavailable kill-switch state produce BLOCK/NO_TRADE.
No fallback value may silently turn an unsafe state into an order.
