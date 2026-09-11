# AMA-46 Factor and Specific Risk Contract

`assess_factor_specific_risk` builds `Σ = B F B' + D` from point-in-time factor
exposures. Each exposure must be available at the information cutoff and have
the same assessment `as_of`; sparse residual history fails closed.

Specific variance is winsorized, shrunk to the approved floor, and clamped to
that positive floor. The assessment retains systematic, specific, and total
variance per instrument, a factor covariance matrix, currency basis, estimation
version, normal full-covariance disagreement, and residual regime sensitivity.
It never treats missing, stale, or zero specific risk as a valid result.

## Production calculation lineage

`assess_factor_specific_risk` remains the deterministic numerical helper. It
does not by itself constitute production evidence and cannot satisfy a risk
acceptance gate. Production callers use `FactorRiskCalculationRepository`.
The repository requires a persisted runtime run and reads only cutoff-visible,
total-return `am_temporal_observation` records whose dataset manifests belong
to that runtime's ingestion. It requires each return date to resolve to an
open persisted exchange session at the same cutoff. Estimator inputs are first
published as a separate append-only evidence record and require the runtime's
selected, active `RISK_ESTIMATION` model authorization; calculation accepts
only that evidence ID. It binds the raw observations, calendar, currency basis,
missingness policy, transformed return panel, estimator evidence, and output
assessment into one content-addressed, append-only record.

Replay rechecks the runtime facts, every observation hash, and every manifest
hash before recomputing the assessment. An absent, stale, conflicting, deleted,
or tampered source/record fails closed with
`FACTOR_RISK_ESTIMATION_LINEAGE_UNVERIFIED`; a fixture or in-memory result is
not interchangeable with the persisted record. The persisted estimator policy
also contains explicit maximum exposure and return-panel ages. The normal
full-covariance comparison is calculated from that persisted PIT return panel,
never supplied by a caller.
