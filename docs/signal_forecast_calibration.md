# AMA-105 — Signal-to-Forecast Calibration

SignalForecastCalibrator converts a versioned Signal value into a horizon-specific forecast-return
component. It does not treat a score as an expected return and it never creates a portfolio or
order. Historical training samples, OOS validation samples, and the target snapshot must share the
Signal identity, historical universe manifest, horizon, and code revision.

The training outcome must be available before validation begins. Validation outcomes must be known
by the target information cutoff and the explicit evaluation time. Linear, quadratic, and monotonic
candidate mappings are fitted using training data only and selected with validation MAE. The
implementation keeps every candidate OOS error, calibration curve, regime and bucket stability,
uncertainty interval, confidence, horizon, currency, unit, valid-until value, model version, and
calibration lineage.

Small forecast deviations at or below the configured cost floor shrink to the prior return. Missing
history or coverage, future outcomes, conflicting snapshot lineage, broken time ordering, singular
models, and unavailable evaluation evidence return ABSTAIN without catalog output.
