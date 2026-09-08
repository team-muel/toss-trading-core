# Phase 10 — Data Quality and Source Health

Phase 10 decides whether point-in-time datasets may feed features and decisions. It never
repairs missing observations with zero and never averages conflicting provider values.

## Quality states

The complete contract is `VALID`, `STALE`, `MISSING`, `CONFLICT`, `PRIMARY_PENDING`,
`VENDOR_DELAY`, `ESTIMATED`, `MANUAL`, `BLOCKED`, and `QUARANTINED`. Only a report with
`VALID` and no issues is decision eligible. Estimated, manual, and vendor-delay inputs can
only reduce confidence when policy permits; stale, missing, conflict, primary-pending,
blocked, and quarantined inputs fail closed.

## Checks

`quality.validators` provides four independent groups plus cross-source comparison:

- schema: required fields, declared types, timezone-aware timestamps, finite decimals
- range: non-negative prices and quantities, coherent OHLC, unique logical rows, and
  versioned policy bounds for interest rates
- time: availability cannot precede the event, no future data, chronological events,
  and membership in an open exchange session
- completeness: every required instrument and trading day is present and corporate
  actions are reflected
- cross-source: differences beyond policy tolerance produce `CONFLICT`; both source
  values and their absolute difference remain recorded

The caller supplies the schema, logical key, required universe, exchange sessions,
expected dates, action lineage, and tolerance from versioned policy. Validators do not
invent these inputs.

## Source health

Provider health uses `NORMAL`, `DEGRADED`, `STALE`, `BLOCKED`, and `UNKNOWN` and records
`last_success_at`, `last_failure_at`, lag seconds, error count, schema status, fallback
status, and required operator action. A source with no successful observation is UNKNOWN.
A source beyond its policy freshness threshold is STALE. Invalid schema or quarantined
data makes the source BLOCKED.

## Propagation and decision gate

The enforced sequence is:

```text
Data quality -> Feature quality -> State quality -> Decision confidence -> Risk decision
```

`propagate_quality` returns `NO_TRADE` for blocking quality or stale/blocked/unknown
sources. This prevents a downstream component from turning STALE input into VALID output.
DEGRADED health produces a reduced-confidence result for policy to constrain further.

## Evidence

Quality issues carry a stable reason code, status, field, row key, message, and structured
details. `QualityIssueRegistry` appends canonical JSONL events, deduplicating an identical
issue event by SHA-256 without overwriting prior evidence. Dataset manifests continue to
provide the source/revision lineage defined in Phase 8.

## Completion criteria

- stale sources automatically block feature generation and decisions
- conflicts are logged without silent averaging
- missing values remain missing and are never changed to zero
- quality status reaches feature, state, confidence, and risk gating
- serious quality errors produce explicit `NO_TRADE` reason codes
