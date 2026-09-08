# Crash Recovery Invariants and RPO/RTO

Recovery starts in `READ_ONLY` regardless of the prior runtime mode. A timeout or process crash
does not prove an order failure: unresolved broker orders remain in reconciliation and cannot be
retransmitted by recovery. Every recovery binds a release SHA, durable checkpoint, event watermark,
broker snapshot, replay hash, reconciliation result and optional operator approval.

The policy fixes separate RPO/RTO limits for account/order/fill/ledger, decision/checkpoint,
market/raw data and derived artifacts, and for PAPER, SHADOW or MICRO_LIVE tiers. Missing or failed
broker/replay/reconciliation evidence blocks recovery. A clean replay with no unresolved orders and
operator approval only permits a separate runtime reauthorization; it never restores a prior mode.
