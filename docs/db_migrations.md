# Database migration policy

- Versions are positive, unique, contiguous integers and execute in ascending order.
- An applied migration is immutable; its name and SHA-256 hash are verified on every run.
- Re-running the same migration set is a no-op.
- A database newer than the application is rejected at startup.
- Each migration and its registry insert commit atomically.
- Down migrations and database-file restoration are not rollback mechanisms.
- A correction is a new forward migration that preserves prior migrations and evidence.
- Production backup and restore remain disaster-recovery tools, not schema evolution.

The schema catalog treats `schemas/asset_management.sql` as immutable migration 1
and then loads numbered files from `schemas/migrations/`. Application startup enables
and verifies SQLite foreign-key enforcement before applying the catalog. Startup
fails if the catalog does not begin at 1, has a gap, or an applied hash differs.

Migration 3 adds raw-evidence foreign-key guards, account-snapshot/raw-response
lineage, append-only protections, strict HTTP method checks, and a database-level
ban on LIVE order intents. These constraints remain active even if an application
caller bypasses higher-level validation.

Migration 4 adds the append-only order-state event stream and cumulative execution
snapshot/delta ledger. It enforces raw-response lineage, per-order sequencing, and
idempotent cumulative snapshot identity at the database boundary.

Migration 5 adds evidenced cash and position openings, settlement metadata,
idempotent open-order cash/position reservations, separately classified execution
cash components, and versioned append-only tax lots and disposal events.

Migration 6 hardens phases 3 and 4 with immutable execution-posting contexts,
point-in-time tax-lot and disposal timestamps, and database-guarded manual-cash
authorization. Application and replay validation bind these records to exact broker
identity, raw evidence, chronology, settlement, and policy context.
For pre-v6 rows, tax-lot timing is derived only from the immutable originating
execution or opening timestamp, disposal timing from its execution delta, and manual
authorization from complete existing metadata. Records without such evidence remain
unreconciled and fail closed instead of receiving a fabricated timestamp or approval.

Migration 7 adds approved account-reconciliation tolerance policies, immutable runs
and item comparisons, persistent issues and explicit resolutions, the effective
`RESOLVED` issue view, and the database order-intent reconciliation guard.

Migration 8 adds immutable normalized point-in-time observations, raw-or-manifest
lineage, availability indexes, chronological constraints, and a non-branching
supersession chain for replayable data revisions.

Migration 9 binds every new order intent to the exact reconciled account and runtime
lineage. It adds an approved reconciliation maximum age and blocks missing, stale,
future-dated, cross-account, or cross-run authorizations at the database boundary.

Migration 10 adds the immutable pipeline-stage evidence ledger. Each stage must be
contiguous and resolve to the exact persisted artifact and runtime lineage. New order
intents additionally require the matching verified risk-control evidence.

Migration 11 rejects account snapshots with blank ownership, timezone-naive or
non-UTC observation times, future runtime observations, or raw evidence received
after the claimed observation time.

Migration 12 adds immutable execution-settlement evidence. Each posting date must
come from the exact verified Toss response cited by the cumulative fill snapshot;
missing, malformed, conflicting, cross-account, non-success, or hash-invalid raw
evidence blocks posting. Replay re-resolves the raw value and verifies its stored
lineage and evidence hash.
