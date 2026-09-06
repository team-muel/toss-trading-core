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
