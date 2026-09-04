# Data policy

- Version: `data-v0`
- Raw provider responses are append-only bronze artifacts.
- Normalized point-in-time observations are silver artifacts.
- Features, states, and calculations are gold artifacts.
- Every artifact has a manifest with source, schema version, observed time,
  received time, content hash, row count, and parent manifests.
- Information received after `information_cutoff_utc` is ineligible.
- UNKNOWN, MISSING, STALE, CONFLICT, and UNRECONCILED are preserved, never replaced
  with zero or false.
- Corrections create new artifacts and manifests; historical data is not overwritten.
