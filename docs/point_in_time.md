# Point-in-time observation contract

Phase 6 establishes the time boundary before market, macro, or company models are
implemented. It does not enable order submission.

An observation separates the economic reference period and event time from its
scheduled release, official release, provider timestamp, receipt, ingestion,
availability, and revision times. All stored instants are UTC; `source_timezone`
retains the source convention for audit and display. Timezone-naive input is rejected.

`available_at` is the first instant at which this system may use the value. It cannot
precede the provider timestamp, official publication, receipt, ingestion, or revision.
`AsOfRepository` always receives an `AsOfContext` and applies:

```text
eligible(x, t) := x.available_at <= t.information_cutoff_utc
latest(t)      := arg max eligible(x, t) by available_at
```

No-result and ambiguous-result cases are `MISSING` and `CONFLICT` failures. Revisions
append a new row linked to the exact earlier vintage. Updates, deletes, cross-series
supersession, and branching revision histories are blocked at the database boundary.
Historical replay therefore returns the vintage knowable at the historical cutoff;
current runs return the newer vintage only after its availability time.

Mandatory adversarial tests cover future sentinels, morning use of same-day closes,
pre-release economic revisions, US daylight-saving conversion, delayed receipt,
timezone-naive input, immutable rows, and deterministic repeated queries.
