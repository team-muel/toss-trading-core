# Temporal policy

- Version: `temporal-v1`
- Storage and calculation timestamps are timezone-aware UTC.
- KST and US Eastern are presentation conversions only; source timezone is retained.
- Every run receives an `AsOfContext` containing `as_of_utc`,
  `information_cutoff_utc`, policy version, parameter set, and code revision.
- Investment calculations may not call the wall clock directly.
- Observed time, provider publication time, system receipt time, and effective time
  are distinct fields.
- Replay uses information and policies knowable at the historical cutoff.
- Every normalized observation records `reference_period`, `event_time`, scheduled
  and official release time when applicable, provider source time, system receipt,
  `available_at`, ingestion time, revision time, and the original source timezone.
- `available_at` is never earlier than publication, receipt, ingestion, or revision.
  A query may return only rows where `available_at <= information_cutoff_utc`.
- Corrections append a new vintage linked through `supersedes_observation_id`.
  They never overwrite a prior vintage and revision history cannot branch.
- Financial-calculation entry points require an explicit `AsOfContext`; missing or
  timezone-naive context is an error, not an invitation to use the wall clock.
