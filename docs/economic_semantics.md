# AMA-100: Economic semantics contract

The shared source is `domain/economics.py`. It reuses the ReturnSemanticType,
ReturnUnit, ReturnMetricStatus and RiskContributionType previously defined in
Decision Journal and CurrencyBasis previously defined in risk/models.py.
Old import paths re-export the same enum objects. Existing journal values,
schema version and hashes do not change merely because the type moved.

## Return meanings

| Type | Meaning | Unit |
|---|---|---|
| PRICING_BASELINE_RETURN | Model-implied expected total return, never a personal hurdle | TOTAL_RETURN |
| FORECAST_TOTAL_RETURN_GROSS | Asset-class total forecast before explicit drags | TOTAL_RETURN |
| FORECAST_TOTAL_RETURN_NET | Forecast after transaction/tax/FX drags | TOTAL_RETURN |
| MODEL_RELATIVE_ALPHA | Net forecast minus an applicable pricing baseline | EXCESS_RETURN (model-relative, not risk-free excess) |
| EXPECTED_BENCHMARK_ACTIVE_RETURN | `(w-b)' forecast_total_return` | ACTIVE_RETURN |
| REALIZED_ACTIVE_RETURN | Realized portfolio minus benchmark total return | ACTIVE_RETURN |
| REGRESSION_ALPHA | Ex-post factor-regression intercept | REGRESSION_INTERCEPT |

The legacy EXCESS_RETURN enum spelling is retained for journal compatibility;
the semantic type distinguishes model-relative residual from risk-free excess.
No generic alpha or ex_ante_active_return alias is accepted by EconomicValue.
AMA-101 must migrate old model outputs explicitly, rather than reinterpret stored
values. Bond/cash/commodity forecasts do not require equity pricing baselines.
Where a pricing model is inapplicable, a missing baseline/alpha stays null with
NOT_APPLICABLE. Post-horizon evidence can stay NOT_MATURED. Neither permits arithmetic.

## Context and numeric boundary

EconomicValue requires semantic type, finite Decimal or explicit missing status,
actual currency (USD/KRW), currency convention (LOCAL/BASE/HEDGED), forecast horizon
(21/63/126/252), matching unit, formula version and reference version. A BASE label
alone cannot identify a currency. Unknown context fails closed with DataQualityError.
The schema is economic-value@1 and serializes numbers as decimal strings.

Model-relative alpha requires net forecast and a pricing baseline with the same
currency/basis/horizon. Benchmark-active arithmetic accepts instrument-keyed
forecasts and exact matching long-only portfolio/benchmark weights summing to one.
Mixed gross/net forecasts and model-relative alpha substitutions are rejected.
The benchmark reference is mandatory. These are arithmetic contracts, not proof
of mandate approval: existing InvestorMandateRegistry remains the authority.
Upstream data quality, PIT, version approval and lineage remain required.

Variance contribution `w_i(Σw)_i` uses RETURN_SQUARED and sums to portfolio variance.
Volatility contribution `w_i(Σw)_i/σ_p` uses RETURN and sums to portfolio volatility.
Money and return values cannot be compared directly; require_same_unit also checks
currency/horizon and availability. Monetary utility comparisons must explicitly
convert a return change via NAV and record that conversion's formula/lineage.

## Accounting and investment invariants

- NAV reconciles cash, securities, settlement receivables/payables and other net
  assets, after checking which items broker balances already include.
- Broker buying power is an external constraint, never cash or an NAV asset.
  Executable buy limit is the minimum of aligned internal free cash and broker
  buying power; unknown/negative values cannot produce a normal result.
- Investable capital deducts only planned outflows/reserves not already recognized
  in NAV. Capital risk budgets are explicit investor-policy allocations.
- Per-share growth already includes buyback/dilution effects. Separate buyback
  components are permitted only in an explicitly versioned aggregate-growth model.
- Forecast component sums must reconcile to total-return identity; explicit net
  costs cannot be charged again in the optimizer.
- Absolute utility uses total forecasts; active utility uses active weights with
  total forecasts. Model-relative residuals require separate benchmark/factor
  neutrality evidence. Pricing baseline is not a second covariance risk penalty.
- Reverse DCF reports conditional assumption surfaces; a scalar inversion must
  state fixed assumptions. It creates no order or standalone active-return authority.
- Black-Litterman equilibrium Pi is excess return. Total-return conversion requires
  an aligned risk-free component. Re-subtracting CAPM baseline is not an automatic
  alpha calculation; BL delta and downstream risk preference have distinct authority.

## Acceptance and remaining adoption

This change establishes the shared boundary and compatibility imports. It does
not certify every historical M4–M8 output as migrated. AMA-100 stays In Progress
until integrated acceptance is satisfied; AMA-101 owns pricing/scope, accounting,
equity decomposition, risk/state schema and stored-row migrations. M4/M5 gap
validation precedes resuming M6. No real-order permission is introduced.

Preflight on 2026-09-07 at base 4948e83: Gate A/B/C/D1/D1.5 unit/negative tests
60 passed. Open feature PRs #15–58 (with gaps) reported successful Python 3.11/3.12
checks and CLEAN merge status. These are branch/fixture observations, not merged
release approval or actual provider readiness. Historical Gate artifacts remain
bound to their original revisions. Existing PRs were not merged by this change.
