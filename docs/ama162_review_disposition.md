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

## Current-master P1 follow-up — 2026-09-23

The implementation has been replayed on current `master` and closes the three
remaining P1 findings:

- Every consumed temporal observation is checked against its pinned manifest's
  schema, manifest identity, cutoff, and event-time boundary before numeric
  projection. Group classifications are loaded from the same PIT observations;
  caller-provided labels cannot establish provenance.
- Research receipt inputs use a sparse delta journal with a 256 MiB encoded
  evidence limit. Reaching the limit fails closed. Replay verifies every
  reconstructed snapshot against its committed input hash.
- Evaluated cross-sections pass through the existing history simulation kernel
  with strict session-axis, type, and finiteness checks. The receipt remains
  `MECHANISM_ONLY`, carries no performance metrics, and is not OOS or operational
  acceptance evidence.

Current local verification: the focused research suite passes 102 tests and the
complete repository suite passes 1,543 tests. Current-head CI and a fresh
independent review are required before this implementation is accepted. No
real-data OOS, production data, runtime, forecast, or trading evidence is
claimed.
