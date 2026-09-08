# AMA-101 remediation progress

This change reuses existing pricing equations, return aggregation, Euler risk
calculation, MoneyTranslation and StateEngine. It is an incremental remediation,
not a claim that the entire M4/M5 acceptance or all downstream adoption is complete.
AMA-100/101 stay In Progress until review, integration and remaining acceptance.

## Implemented corrections

- CAPM@2 and MULTIFACTOR@2 have canonical pricing-baseline entry points and
  pricing-result@2 outputs. Both require fresh registry authorization for the
  PRICING_BASELINE_RETURN scope and an explicit EQUITY/EQUITY_ETF asset scope.
  Output includes currency, currency basis, horizon, semantic type, unit and formula
  version. Registry definitions granting this scope cannot also grant another
  output or position/benchmark authority. A legacy REQUIRED_RETURN token cannot
  authorize v2. The formulas are reused; no new estimator was introduced.
- pricing-result@1 and old function names remain explicit legacy contracts for
  compatibility/replay. They are not v2 authority. Existing stored hashes are not
  rewritten. Model registry migrations require a new model version and approval,
  not automatic promotion of an old token.
- economic-migration@1 creates new records bound to the full legacy source hash,
  deprecated field and legacy contract version. Pricing source output hashes and
  alpha = net forecast - baseline are rechecked. Currency/basis/formula/reference
  are explicit inputs. Unknown ex_ante_active_return meanings are rejected rather
  than guessed. This is data conversion, not proof of source truth/model approval.
- expected-return@2 uses four per-share equity components by default, with no
  separate buyback. Aggregate growth requires AGGREGATE mode and distinct
  aggregate_fundamental_growth/net_buyback_yield names. Old ambiguous growth names
  fail rather than silently acquire a new interpretation. Bond/cash/commodity
  forecast aggregation continues to run independently of equity pricing.
- RiskContribution.payload() is risk-contribution@2 with separate variance and
  volatility contribution arrays and squared-return/return units. Legacy
  component/marginal attributes retain their volatility meaning. Invalid numeric
  inputs or non-reconciling volatility components fail closed.
- PortfolioStateEngine defaults to portfolio-state@2 component names. Explicit
  portfolio-state@1 replays the old risk_contribution component unchanged. New
  construction rejects the old key set. Existing snapshot serialization is
  preserved; component names and code revision remain bound into state identity.
- reconcile_accounting_nav handles explicit broker field inclusion and settlement
  receivable/payable signs. Fields already inside another root are excluded once;
  buying power is always excluded from assets. Each relation requires a source
  evidence ID. Duplicates, unknown/nested inclusion, currency conflicts and mismatch
  with reported NAV fail closed. This helper reuses MoneyTranslation FX behavior.

## Remaining adoption before Gate D2/E

Decision Journal v2 now permits explicit inapplicable pricing with no pricing lineage and
no model-relative alpha, while requiring aligned return contexts. Legacy v1 replay retains
the original hashes and mandatory pricing contract. Outcome serialization is unchanged.
This closes the journal format restriction; upstream applicability evidence still needs
integration through the common decision path.

| Area | Remaining work |
|---|---|
| AMA-9/10 | Period accounting consumes reconciled opening/closing NAV; cash constraints now bind verified Toss raw responses to existing CashLedger openings/events/reservations. Reviewed Toss 1.2.14 fields do not establish accounting NAV/settlement inclusion: a separately verified accounting source and operational snapshot adoption remain required. See toss_accounting_evidence.md |
| AMA-38/41/43 | Register/approve actual v2 models; propagate currency/horizon facts from risk-free and factor inputs (AMA-40/42), beyond explicit output metadata |
| AMA-44/45 | Migrate historical expected-return stores; wire typed canonical forecasts and applicable model-relative alpha through the common decision path; legacy calculate_alpha is not the new asset-scope entry point |
| AMA-36/47 | Bind contribution currency/horizon/model lineage in actual state construction; migrate versioned stored datasets without changing old hashes |
| AMA-57/60 | Recheck governor authority and cost units; bind journal applicability to upstream model/asset scope evidence in the common decision path |
| AMA-50/61 | Build/revalidate integrated Gate D2/E evidence against current contracts; earlier fixture PASS is insufficient |

expected_return.v1.schema.json preserves the prior serialization contract.
expected_return.schema.json now identifies expected-return@2. New pricing, migration,
NAV and risk schemas are packaged with this change. No production store migration
or real-order activation is performed by these library changes.
