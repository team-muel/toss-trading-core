# ADR 0004: Storage separation

Status: Accepted. Transactional account and ledger state uses SQLite during development.
External datasets use bronze/silver/gold artifacts and immutable manifests. Decision
artifacts use an append-only journal. Cache tables are disposable and non-authoritative.
