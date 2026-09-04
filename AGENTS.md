# Codex repository rules

These rules apply to the entire repository.

1. Preserve the runtime dependency order: investment policy -> account truth ->
   time truth -> data truth -> financial calculation -> target portfolio -> risk
   control -> order.
2. One independent feature belongs on one `codex/` branch and one pull request.
   Do not mix unrelated refactoring with a feature.
3. Never deploy or enable real orders without explicit user approval in the
   current task. Documentation, simulation, and read-only checks are not approval.
4. Every feature includes happy-path and failure-path tests. Unknown, stale,
   conflicting, missing, or unreconciled inputs fail closed.
5. Change schema, code, tests, and documentation together when a contract changes.
6. `live_trading_enabled` remains `false`. Do not weaken this default.
7. Never treat a placeholder, empty implementation, fabricated value, or TODO as
   a successful normal result.
8. When ownership or responsibility is ambiguous, produce `NO_TRADE` and an
   explicit reason code.
9. Preserve user changes. Do not perform destructive Git operations.
10. CI must pass before merge. Do not bypass, skip, or silently relax failing checks.
