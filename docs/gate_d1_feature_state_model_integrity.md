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
IDs. The revision must resolve to the executing module's checkout HEAD and tree. The gate derives
the repository from the module's resolved path, resolves Git internally, constructs the source
verifier internally and invokes its concrete implementation. Optional legacy verifier arguments
must have the exact concrete type, the same resolved repository root and the default Git setting;
subclasses, unrelated clean clones and caller-selected executables fail source verification.
An installed wheel without a verifiable executing Git checkout cannot establish this source identity.
The verifier also rejects staged, unstaged, and non-ignored untracked files, or an unsuccessful
Git status read; a committed tree does not identify the executed code in a dirty checkout.
Git subprocesses discard inherited `GIT_*` variables, explicitly select the executing worktree,
and disable optional locks. Repository/worktree/config environment overrides and local
`core.worktree` cannot redirect verification to a clean copy. Normal host configuration remains
available for established line-ending semantics.
Each `sha256:`
artifact must be the exact canonical bytes of a `feature-state-model-gate-evidence@5` catalog record
for the named check, revision/tree, replayed canonical runtime-bundle hash/catalog ID, and the
runtime-bound external-attestor-registry snapshot/hash. `CanonicalD1RuntimeAuthorityVerifier` verifies
that registry against the code-bound canonical Cloud KMS authority and the runtime's persisted
information cutoff; the registry binding must already exist no later than that cutoff. It also requires
an independently signed, append-only D1 runtime attestation issued after that binding and no later
than the cutoff, for the exact bundle hash/catalog ID, runtime revision, canonical `gs://`
immutable-store URI, and the exact seven semantic check results and artifact hashes. The signed
`canonical-d1-runtime-attestation@2` payload freezes both `passed` and the sorted artifact IDs for
each check. Gate evaluation requires exact equality to those signed checks. Local cached bytes
are accepted only for a signed artifact hash and must still match the canonical body and digest;
a caller cannot create new check labels under an existing bundle signature. Version-1 attestations
without signed check hashes are rejected. Version-5 artifact bodies include `passed=true`, the
source tree, bundle, registry and store URI, and omit the attestation hash to avoid circular hashing.
The gate result retains the attestation identity. A
caller-provided subclass of the runtime authority verifier is rejected; the gate invokes the exact
concrete verifier implementation rather than dispatching to a caller override. A
copied registry therefore cannot authorize a synthetic local runtime. A caller-supplied label, test
selector, mutable file, fixture-only catalog, local SQLite file, or historical result cannot produce
PASS without that independently signed authority.

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
Every persisted pricing run for the selected runtime must have expectation evidence. An incomplete
sibling pricing run cannot disappear behind a complete chain for the same state; record and replay
reject the missing coverage before publishing or changing the bundle.
Recording assembles and compares an existing runtime binding before publishing a catalog object.
A conflicting retry leaves both the existing evidence row and catalog bytes unchanged; an identical
retry verifies the existing catalog rather than repairing or replacing it.
