# Database migration policy

- Versions are positive, unique, contiguous integers and execute in ascending order.
- An applied migration is immutable; its name and SHA-256 hash are verified on every run.
- Re-running the same migration set is a no-op.
- A database newer than the application is rejected at startup.
- Each migration and its registry insert commit atomically.
- Down migrations and database-file restoration are not rollback mechanisms.
- A correction is a new forward migration that preserves prior migrations and evidence.
- Production backup and restore remain disaster-recovery tools, not schema evolution.
