# AMA-106 — Forecast Combination and Diversification

ForecastCombiner combines calibrated Signal forecast overlays only. It does not incorporate the
asset-pricing baseline and does not emit a portfolio or an order. Each source carries its forecast
calibration ID, Signal run ID, AMA-104 neutralization ID, OOS evidence time, coverage, uncertainty,
confidence, incremental IC, stability, turnover, and implementation cost.

All sources must refer to the same PIT universe, as-of time, cutoff, currency, unit, horizon, and
validity window. OOS evidence must already be available at the cutoff and at evaluation. A versioned
parameter registry records the maximum source weight, cost penalty, formula version, and parameter
set. Conflicting configuration at an existing version is rejected.

Weights combine incremental information, confidence, stability, cost, and average forecast
correlation. They are capped to prefer a stable plateau rather than a single-period winner. The
published component preserves source and neutralization lineage, individual contribution, correlated
combined uncertainty, expected implementation cost, and the effective independent forecast count.
Missing lineage, future evidence, malformed matrices, unavailable parameters, or insufficient
information return ABSTAIN without catalog output.

## AMA-133: validity application at evaluation time

The combiner applies the shared `SignalValidity.effective_weight` once to the
combined **gross forecast**, before subtracting execution costs. Applying decay
to normalized source weights would cancel the common discount and is not used.
Execution costs and the covariance-derived uncertainty budget are not reduced
merely because the information is older. The resulting uncertainty treatment is
conservative, not a claim that forecast errors shrink with age.

Both the report and each component carry `evaluated_at` and a
`forecast_validity_decay` record with contract version
`forecast-validity-at-evaluation/v1`, stage, original production time,
application time, and applied weight. The source `as_of` and information cutoff
remain lineage; numerical forecast values describe `evaluated_at`. These fields
are included in the content hash and published JSON schema. A consumer must not
apply the same absolute decay again. Re-evaluation should recompute from the
immutable raw sources, not wrap an already-decayed component as a fresh raw
forecast. This stage is distinct from expression, research-position, and IC decay.

All decay profiles have an exclusive `valid_until` boundary. Both covariance and
correlation must be PSD. The PSD predicate uses exact rational Schur complements
of the finite Decimal inputs, avoiding sqrt-rounding false rejections of valid
singular matrices. Validated input matrices are detached from mutable caller data.
