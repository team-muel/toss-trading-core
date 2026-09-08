# AMA-48 — VaR, CVaR, FX and Liquidity Risk

Historical VaR/CVaR uses positive-loss return units. `TailRiskAssessment` keeps the
confidence level, currency basis, return horizon and formula version with the result.
Expected return, risk-free rate, covariance, factor return and active return must all use
the same `CurrencyBasis` and horizon; a mismatch is rejected before risk calculation.

Liquidity uses two independent capacity limits in one explicit currency:

* `order_capacity = ADV × order_participation_rate`
* `position_capacity = ADV × liquidation_participation_rate × allowed_liquidation_days`

The assessment retains spread, volatility, expected liquidation cost, session, confidence,
return/liquidity horizons and formula version as input context. A known breach is `BLOCKED`
with the relevant reason code. `UNKNOWN`, `MISSING`, `STALE`, and `CONFLICT` inputs return a
blocked result with `None` capacities and cost rather than fabricated zero values.

This module produces risk measurements and constraints only. It does not generate an order or
enable live trading; `live_trading_enabled=false` remains in force.
