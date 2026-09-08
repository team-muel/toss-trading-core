# AMA-59 — Human Override and Manual Intervention Governance

`ManualOverride` is an immutable, append-only state event bound to one existing decision. It
records `override_id`, `decision_id`, original and override actions, reason, requester, approver,
creation time, and expiry time. The default expiry is 24 hours and is written into the event; an
override cannot be open-ended by omission.

The permitted manual actions are restrictive interventions only: `BLOCK`, `DEFER`,
`LIQUIDITY_RESERVE`, and `EMERGENCY_LIQUIDATION`. They cannot create `ALLOW` or `REDUCE`, so a
manual event cannot replace risk authorization or bypass the downstream risk and order controls.
`EMERGENCY_LIQUIDATION` remains a state instruction, not an order, quantity, price, or broker call.

`ManualOverrideJournal` writes canonical JSONL events and accepts exact replay idempotently. Each
event ID is the SHA-256 hash of its complete authority and time-window payload. Tampered IDs,
unknown event shapes, duplicate IDs, conflicting simultaneous overrides, and a mismatch between
the event's original action and the decision being resolved fail closed. Expired events have no
effect but remain in the journal so historical replay observes the same intervention window.

The output of `resolve` is `ManualInterventionState`, which preserves the immutable original
decision and separately names the effective manual state. The next decision-journal phase can
include this state event in its economic record without mutating historical decisions.

The serialized event contract is [manual_override.schema.json](../schemas/manual_override.schema.json).
This feature does not enable live trading; `live_trading_enabled` remains false.
