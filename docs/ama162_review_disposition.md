# AMA-162 / PR #78 review disposition

The original reviewed head is `197a7ae8d9e6e4629b43cfbba4def31ed1a59872`.
This record describes the two Codex P2 findings and their regression evidence;
it is not a substitute for final-head review or protected merge-queue checks.

## R1: complete replay coordinates

A canonical RepositoryPanelResolver permits known extra instruments outside the
historical universe. Their masked field columns are still snapshotted and hashed.
The receipt now records `resolver_instrument_ids` separately from the current
`instrument_ids` so the input hash can be reproduced. The regression constructs
fresh resolvers from those coordinates and the pinned repositories and verifies
every session input hash and the complete run receipt hash.

## R2: fail closed on incomplete expression groups

Simulation neutralization classifications do not validate the separate historical
expression group panels. `group_rank` and `group_neutralize` must now have a
nonblank string classification for every historical member in every period,
including warm-up. Missing or malformed classifications cannot fall through to
the operators' `__ungrouped__` bucket. Nonmembers do not need classifications.

## Evidence

The new cases include missing keys, nulls, blank/whitespace values, numbers and
booleans at warm-up and effective periods, both group operators, complete-group
positive controls, nonmember handling and full extra-instrument replay.
The original implementation failed 25 new cases. The corrected implementation
passes all 28 added cases. Together with the existing campaign suite: 88 passed.
A dependency-complete local Python 3.13 run passes all 1,449 repository tests.
Exact-head remote CI and review disposition are recorded on the PR and in Linear.

No forecast/order authority, schema migration, runtime deployment, data-source
permission, production portfolio weight or live flag is introduced by this fix.

## Follow-up R3/R4: original session order and consumed vintages

Codex re-review of `aed0fb9` found that sorting session instrument IDs loses
the floating-point reduction order used by the simulator, independently of
the resolver order. It also demonstrated that canonical observation append
can add a newly imported vintage with historic availability under the same
pinned manifest. A later repository query can therefore select another value
without modifying that manifest.

The receipt now preserves the session order and effective timestamp, and stores
the exact consumed field panels alongside their hashes. Two additional tests
fail on the earlier head: replay with different session/resolver orders and
replay after a canonical backdated observation append. Frozen receipt replay
recovers the original result rather than falsely claiming a latest query is
immutable. Campaign tests: 90 passed. Full local suite: 1,451 passed.

This additive schema correction is made before the initial API is merged or
consumed in production. Receipts remain mechanism-only, not signed approvals.

## Third review R5/R6/R7: bounded retained inputs and typed source evidence

Codex review of `79d893f` found three P1 issues. They were reproduced by a
wrong-schema observation append under a valid manifest, complete but unsourced
caller classifications, and the full-prefix v1 receipt representation.

* The runner now evaluates one complete input prefix at a time and retains only
  each last raw cross-section for the existing history simulation kernel. The
  input receipt v2 stores sparse changes, not all expanding numeric prefixes.
  `iter_session_inputs` rehydrates and hash-checks one snapshot at a time. A
  256 MiB encoded-input-journal budget fails explicitly rather than allowing
  unbounded accumulation. Stable histories have linear retained input evidence;
  genuinely revised historical values cost their actual changed cells. CPU work
  still includes full-prefix evaluation, and caller-owned session structures
  are outside this retained-input optimization. No full-scale speedup is claimed.
* `field_with_evidence` reads full canonical TemporalObservations before numeric
  projection. Every consumed record must match the pinned manifest's schema and
  identity and remain within the temporal cutoff. Observation IDs/content hashes
  are bound into per-instrument evidence hashes without repeating all row metadata.
* Every expression group label is checked against schema-validated observations
  in each historical session's own pinned manifest. Complete caller dictionaries
  do not establish provenance. Simulation group neutralization declares its
  canonical `neutralization_group_field` and receives the same verification.
  Missing, future, mismatched or malformed classifications fail closed. The
  first version requires explicitly materialized classifications per reference
  period; it does not invent a forward-fill/effective-dating policy.

Three original regressions fail on `79d893f`; ten third-round tests pass after
correction. Campaign suites: 100 passed. Full dependency-complete local suite:
1,461 passed. Incremental-history tests verify sparse linear encoded growth,
revision replay, budget rejection, input hash validation and identical outputs
through the existing simulation kernel. Earlier four review fixes remain tested.
Final-head remote CI and Codex disposition must still pass before protected merge.
