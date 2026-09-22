# Gate D1 — Feature, State, and Model Integrity

Gate D1 is the M3 promotion boundary. It accepts a versioned set of evidence only when every
Feature, State, calculation-lineage, quality-propagation, and model-governance condition passes.
An unknown, absent, or failed check makes the result `FAIL`; a failed result must not promote M4
execution work.

The required checks are:

- historical feature standardization, cross-sectional transforms, and missing/future inputs are PIT
  and fail closed;
- all forecast and holding horizons, validity windows, and decay profiles are compatible;
- Market, Company, Portfolio, and System snapshots are deterministic and separately reproducible;
- every final calculation trace reaches a verified immutable Bronze raw manifest;
- quality, source freshness, and confidence reach Feature, State, and decision constraints;
- an active model needs current registry authorization for its approved scope; and
- identical input, code/model version, and policy produce the same state identity and model output hash.

`FeatureStateModelIntegrityGateInput` requires the exact check set, one `runtime_run_id`, an exact
full Git revision for both the evaluation and the producing evidence, and immutable catalog artifact
IDs. The revision must resolve to the verifier checkout's current HEAD and tree. Each `sha256:`
artifact must be the exact canonical bytes of a `feature-state-model-gate-evidence@3` catalog record
for the named check, revision/tree, replayed canonical runtime-bundle hash/catalog ID, and the
runtime-bound external-attestor-registry snapshot/hash. `CanonicalD1RuntimeAuthorityVerifier` verifies
that registry against the code-bound canonical Cloud KMS authority and the runtime's persisted
information cutoff. A caller-supplied label, test selector, mutable file, fixture-only catalog, local
SQLite file, or historical result cannot produce PASS without that independently signed authority.

The result has canonical content hashing, so a recorded PASS can be reproduced without trusting a
mutable status flag. The historical 2026-09-06 record contains test selectors and a short revision;
it is implementation history, not current D1 acceptance authority. This gate validates evidence
identity only: it does not manufacture the required runtime Feature/State/Model artifacts. The
gate does not enable trading and `live_trading_enabled` remains `false`.

`CanonicalD1RuntimeEvidenceRepository` and its CLI are the separate source-bundle boundary.  They
can record or replay only one already-persisted `runtime_run_id`, its immutable manifest-to-Bronze
lineage available no later than that run's information cutoff, Feature/State/calculation hashes,
and review-backed runtime model-registry binding.  They
return `BLOCKED` for missing, ambiguous, non-PIT, or unreplayable sources and never emit a D1 PASS.
The seven semantic checks still require their own real canonical runtime evidence; a test fixture,
synthetic input, catalog bundle, or historical GitHub result is not acceptance evidence.
Replay is read-only: a missing runtime-bundle catalog object is a failure, never an instruction to
republish it.
