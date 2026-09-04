# Promotion policy

- Version: `promotion-v0`
- Each stage requires recorded, repeatable evidence and explicit approval.
- Read-only requires complete account snapshots and reconciliation diagnostics.
- Replay requires deterministic results from immutable inputs.
- Paper requires exact fills, cash, positions, settlement, and failure-path tests.
- Shadow requires comparison with live broker constraints without transmission.
- Semi-auto requires human approval for every order batch.
- Micro-live requires explicit user authorization, bounded capital and instruments,
  a tested kill switch, rollback procedure, and successful incident drill.
- Passing return metrics alone can never promote a stage.
