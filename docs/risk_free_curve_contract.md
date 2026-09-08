# AMA-40 Risk-free Curve Contract

`RiskFreeCurve` accepts exactly the 21, 63, 126, and 252 business-day decision horizons for one `as_of`, currency, day-count, compounding, and formula version. It does not infer a missing tenor, currency, convention, uncertainty, or quality.

`return_for` emits `RiskFreeReturn`, containing `currency`, `forecast_horizon`, `annualized_rate`, `holding_period_risk_free_return`, `day_count`, `compounding`, `as_of`, `formula_version`, `source`, `quality`, and `uncertainty`. The only currently accepted convention is `BUS/252` with `EFFECTIVE_ANNUAL` compounding: `(1 + annualized_rate)^(forecast_horizon / 252) - 1`.

`require_risk_free_alignment` and the `*_pricing_baseline_from_risk_free` entry points require the same currency and forecast horizon as the pricing calculation, and reject future-as-of curve values. A raw rate remains available only for the legacy v1 pricing functions; canonical v2 baseline entry points use the typed curve output.
