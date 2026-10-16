# Three-theme research reconstruction

Decision: 2026-09-09, Linear AMA-161. Implementation stages: AMA-162 through
AMA-167. Research provenance is recorded in AMA-157 through AMA-160; their Done
state does not assert predictive value, live readiness, or completed migration.

## Ownership and migration order

1. AMA-162: bind hypotheses to canonical expressions and reproducible run receipts.
2. AMA-163: migrate quant factors, starting with scale-invariant momentum.
3. AMA-164: adapt ALFRED vintages to canonical PIT observations and macro states.
4. AMA-165: adapt fundamental source/driver evidence and reconciled numeric features.
5. AMA-166 / AMA-134: preregistered OOS diagnostics and existing Signal/Forecast integration.
6. AMA-167: consumer cutover and parity-backed retirement, coordinated with AMA-156.

Data/time/reference/quality and economic feature truth remain in
`asset_management`; expression/operators/research simulation remain in
`alpha_management`. The outer research platform coordinates runs and persistence.
This change does not duplicate FeatureDefinition, BacktestRunSpec, a forecast
engine, a data store, or a production runtime. Core asset-management modules must
not import this research layer. The existing outer research bridge is unchanged.

The three themes share provenance and validation contracts, not an arbitrary
common score. Macro states common to every instrument must not be cross-sectionally
ranked. Fundamental evidence builders are not required to be DSL expressions.
Combine independently validated forecasts under the existing AMA-106 contract;
a raw-score composite is a separately preregistered research experiment.

## Implemented first slice

`alpha_management.campaign` exposes `ResearchSpec`, `ResearchRun` and
`run_expression_research`. `alpha_management.quant.momentum_spec` is a small
reviewed template, not a second factor DSL or backtester.

```python
from alpha_management.expression import AlphaSimulationSettings
from alpha_management.quant import momentum_spec
from alpha_management.campaign import run_expression_research

spec = momentum_spec(
    field="total_return_index",
    input_contract_key="governed-usd-total-return-index-v1",
    lookback=63,
    settings=AlphaSimulationSettings(universe="approved-etf-universe", delay=1),
    evaluation_horizon_sessions=21,
    policy_version="approved-research-policy-v1",
    dataset_source="approved-source",
    dataset_name="approved-index-dataset",
    dataset_schema_version="approved-index-schema-v1",
)
# The outer data/experiment owner constructs HistoricalSessions from canonical
# RepositoryPanelResolver + PointInTimeDataSource. Every context binds
# parameter_set_id=spec.spec_hash and policy_version=spec.policy_version.
# Include warm-up sessions from the beginning of each panel's reference axis.
# run = run_expression_research(spec, sessions)
# Persist run.evidence_json and run.evidence_hash alongside existing artifacts.
```

The example names are contract placeholders, not shipped data providers, schema
approvals, or a runnable real-market campaign. Tests use real immutable, reference
and temporal repositories with explicitly synthetic observations.

### Momentum semantics

`rank(ts_return(total_return_index,63))` ranks simple 63-observation returns:
`index[t] / index[t-63] - 1`. It is not `rank(ts_delta(close,63))`: an absolute price
difference changes when the price unit changes. `ts_return` does not turn raw
prices into a dividend-adjusted series or establish split, currency or calendar
truth. Those facts must be established by the upstream dataset contract.

The lag is a positive integer (bounded by the existing DSL limit). The first lag
observations are unavailable. Any missing observation in the entire lag+1 window
keeps the result unavailable; missing is never zero. Nonpositive inputs, boolean
numeric substitutes, nonfinite inputs and nonfinite outputs fail closed. The
existing function-call grammar is unchanged: no general arithmetic syntax was
introduced. Price-unit invariance applies to consistently rescaled input series.

The other quant families, macro-state builder and fundamental-evidence builder
are not implemented by this slice. A theme enum is metadata, not model support.

## Research specification versus experiment approval

A spec binds theme, family/version, rationale and falsification criteria to one
canonical expression, its exact field/group dependencies, simulation settings,
evaluation horizon and upstream dataset source/name/schema. It defensively copies
mutable input containers and includes all these declarations in its hash.

The data source/name/schema tuple is checked against actual pinned manifests.
Field contract keys record the economic declaration, but a name or hash alone
cannot prove that a provider supplied correctly adjusted data. Dataset/feature
approval stays with its existing owner. Likewise, the horizon is a research
objective, not an OOS test window or an assumed expected return.

`BacktestRunSpec` and the existing validation registry retain preregistration,
train/test windows, repeated-trial accounting, matured outcome requirements and
approval authority. Do not promote an expression spec as a substitute.

## Execution and evidence contract

The runner accepts canonical repository resolvers and PIT sources, not arbitrary
objects claiming manifest IDs. It verifies spec/context binding, chronological
sessions and nondecreasing cutoffs, one code revision, pinned silver/VALID
manifests, declared dataset identity and canonical universe membership at every
historical session. Each reference column must match the corresponding input
session, including warm-up. The outer owner must still supply an actual governed
exchange calendar: internal timeline consistency alone cannot certify that a
caller did not omit the same trading day from every supplied representation.

The runner snapshots field and group reads before calling the existing
`simulate_history`. Expression decay, signal-session delay and research-position
decay keep their current owners. It retains raw scores and research-only weights;
none is an order, production weight, calibrated forecast or permission grant.

The receipt records the spec, spec hash, consumed-input hashes, replay coordinates
for *every* session (including unavailable warm-up points), contexts, manifests,
reference periods, memberships, group classifications and canonical result hash.
The exact consumed field values and both instrument-axis orders are frozen in
the receipt. Retain the upstream immutable artifacts as provenance. Replaying
a receipt uses its frozen panels, not a latest-vintage query that may change
after a later canonical append under the same manifest. The receipt is content-addressed,
not signed: an ordinary Python object or a recomputed hash is not an approval.
External import/authentication and durable storage belong to the outer owner.

`validation_scope=MECHANISM_ONLY`. `COMPUTED` means at least one raw score exists,
not that all positions are ready, that the sample is adequate, or that an alpha
was found. `NO_OBSERVATIONS` is a valid explicit unavailable outcome. This first
runner accepts no forward-return panel and computes no performance metrics;
forward-outcome maturity, economic costs, OOS diagnostics, calibration and
promotion stay in AMA-166/AMA-134 and the existing production contracts.

## What remains protected

Do not delete QuantFactorConfig, MacroRegimeConfig, cost-aware backtesting,
ALFRED vintage logic, source reconciliation, hypothesis/prospective evidence,
multiple-testing protection or operational consumers just because a canonical
expression runner now exists. A research-weight simulator is not automatically a
replacement for a strategy backtest with rebalance, cash, costs and turnover.

AMA-156 owns legacy execution and namespace cleanup. New code in this slice has
no `toss_trading` or `research_platform` imports and makes no assumptions about
which legacy branch has been published. After that change is available, verify
compatibility against its actual tree before consumer cutover. AMA-167 requires
an import/CLI/deployment/package-resource inventory, golden parity or explicit
semantic differences, historical evidence readability and consumer migration
before deletion. Do not resurrect a second account/risk/execution runtime.

## Verification and review boundary

The synthetic suite covers price rescaling, missing/invalid data, stable and
changed identities, defensive copying, real PIT repository replay, delay/decay
parity, group completeness, warm-up coordinates, malformed timelines, narrowed
historical universes, wrong schema and tampered immutable artifacts. Adversarial
self-review added historical membership and manifest-schema checks; that work is
not an independent external review. Existing architecture tests still constrain
imports. Run full required CI and obtain review against the exact PR head.

No live flag, broker authority, account data, migration or live-write authorization
is introduced by the reconstruction. Rollback is a reviewed revert of this finite
change. A passing unit test or CI job never authorizes trading.
