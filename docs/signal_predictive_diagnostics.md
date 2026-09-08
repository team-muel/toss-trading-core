# AMA-103 — Signal Predictive Diagnostics

This module evaluates completed Signal values. It is read-only: no diagnostic can emit an order,
target weight, or live forecast. Every report preserves the Signal run ID, version, historical
universe manifest, formula/parameter contract, code revision, and input content hash.

Cross-sectional observations bind every score and realized forward return to the exact historical
universe manifest used at as-of. Missing Signal values remain explicit so coverage is measured
against the full PIT universe; substituting a current universe is rejected. Each row requires an
information cutoff at or before as-of, an embargo after as-of, and an outcome availability time at
or after the embargo. Evaluation before outcome availability, an insufficient embargo, low
coverage, absent quantile breadth, and zero-variance IC return ABSTAIN without writing a report.

Cross-sectional reports retain Pearson IC, average-tie Spearman RankIC, Q1 through Q5 returns,
gross Q5−Q1 spread, post-cost spread, hit rate, coverage, conservative top-quantile turnover,
holding-period overlap, and sector/size/liquidity stability. Post-cost spread charges both long and
short legs. IC decay stores mean Pearson IC, RankIC, and post-cost spread independently for every
completed horizon with the configured minimum number of observations.

Time-series observations are out-of-sample ETF forecasts and completed returns under the same
embargo contract. They store calibration bias, MAE, RMSE, post-cost realized utility, rolling and
overall correlations, regime-specific error and utility, drawdown/error correlation, and sign
accuracy as a secondary metric. Economic magnitude, cost-adjusted value, and temporal stability
are retained together in the immutable signal-diagnostics catalog.
