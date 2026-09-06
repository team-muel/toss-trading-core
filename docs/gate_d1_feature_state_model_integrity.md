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

`FeatureStateModelIntegrityGateInput` requires the exact check set and immutable evidence IDs.
The result has canonical content hashing, so a recorded PASS can be reproduced without trusting a
mutable status flag. The gate does not enable trading and `live_trading_enabled` remains `false`.
