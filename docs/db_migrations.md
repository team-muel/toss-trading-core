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
