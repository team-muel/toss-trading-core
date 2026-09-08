# AMA-45 Model-relative Alpha Contract

The canonical path accepts a `FORECAST_TOTAL_RETURN_NET` value produced from a
64-character combined-signal-forecast lineage ID and a matching
`PRICING_BASELINE_RETURN`. Both values must have the same currency, currency
basis, and forecast horizon. The alpha model itself must have an active
`MODEL_RELATIVE_ALPHA` authorization.

Confidence shrinks the forecast toward `pricing_baseline + prior` before the
residual is calculated. Thus the stored alpha remains exactly `net forecast -
pricing baseline`; it is never a regression intercept or benchmark-active
return. The result preserves the raw alpha, confidence, prior, shrinkage amount,
combined forecast lineage, uncertainty interval, and an explicit `ABSTAIN` when
the interval crosses zero or uncertainty exceeds policy.
