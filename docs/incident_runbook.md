# Incident runbook

1. Activate the durable kill switch and stop new order intents.
2. Preserve raw broker responses, logs, run IDs, manifests, policy hashes, and clocks.
3. Query Toss for accounts, open/closed orders, fills, balances, and sellable quantity.
4. Mark affected accounts UNRECONCILED; do not infer missing state.
5. Rebuild cash and positions from immutable events and compare with Toss.
6. Record impact, timeline, owner, cause, correction events, and validation evidence.
7. Resume only after reconciliation is MATCH and the applicable promotion authority
   explicitly approves restoration. Never edit historical events to hide a difference.
