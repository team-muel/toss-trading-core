# AMA-104 — Signal Neutralization and Incremental Power

SignalNeutralizer is a read-only PIT cross-sectional calculation. Every candidate and baseline is
an existing SignalSnapshot with the same as-of time, information cutoff, historical universe
manifest, and code revision. The input also binds sector, industry, size, beta, and liquidity
exposures, completed returns, and before/after OOS forecasts to that exact universe.

The calculation records Pearson and rank-correlation matrices, beta and categorical common
exposure, raw IC, partial correlation/incremental IC, and an OLS residualized Signal component.
The transform order is stored explicitly with its formula and parameter versions. It also keeps OOS
MAE improvement, coverage, turnover, and cost before and after the proposed addition. High
correlation is diagnostic evidence only; this module never deletes a Signal or produces an order.

Missing PIT coverage, mismatched snapshots, unavailable outcomes, inadequate embargo, singular
controls, or zero variance return ABSTAIN and publish no catalog object.
