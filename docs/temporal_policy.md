# Temporal policy

- Version: `temporal-v0`
- Storage and calculation timestamps are timezone-aware UTC.
- KST and US Eastern are presentation conversions only; source timezone is retained.
- Every run receives an `AsOfContext` containing `as_of_utc`,
  `information_cutoff_utc`, policy version, parameter set, and code revision.
- Investment calculations may not call the wall clock directly.
- Observed time, provider publication time, system receipt time, and effective time
  are distinct fields.
- Replay uses information and policies knowable at the historical cutoff.
