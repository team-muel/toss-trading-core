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

`FeatureStateModelIntegrityGateInput` requires the exact check set, an exact full Git revision for
both the evaluation and the producing evidence, and immutable catalog artifact IDs. The revision
must resolve to the verifier checkout's current HEAD and tree. Each `sha256:` artifact must be the
exact canonical bytes of a `feature-state-model-gate-evidence@1` catalog record for the named check,
revision, and source tree; caller-supplied labels, test selectors, and mutable files are not evidence.

The result has canonical content hashing, so a recorded PASS can be reproduced without trusting a
mutable status flag. The historical 2026-09-06 record contains test selectors and a short revision;
it is implementation history, not current D1 acceptance authority. This gate validates evidence
identity only: it does not manufacture the required runtime Feature/State/Model artifacts. The
gate does not enable trading and `live_trading_enabled` remains `false`.
