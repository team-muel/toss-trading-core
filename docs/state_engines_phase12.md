# Phase 12 — Four State Engines

Phase 12 keeps market, company, portfolio, and system state as separate contracts. A state
is a versioned snapshot of components with uncertainty and lineage. It is not an order or
investment signal.

## State snapshot v2

`state-snapshot-v2` strengthens the State boundary so a future regime model cannot infer
meaning from an untyped number. Every snapshot records both `as_of` and
`information_cutoff`; every component must use exactly the same temporal context as the
snapshot. The state identity is a deterministic SHA-256 over the complete component
semantics and lineage, the state policy, times, code revision, and the still-temporary
optional regime field.

Every `StateComponent` records:

- a stable `component_id`
- the component value
- an explicit `semantic_type` and `unit`
- one of `RAW`, `Z_SCORE`, `PERCENTILE`, `DIRECTIONAL_SCORE`,
  `STANDARDIZED_COMPOSITE`, `STRUCTURED`, or `CATEGORICAL`
- `as_of` and `information_cutoff`
- confidence, quality and freshness
- immutable evidence IDs
- parameter-set and formula versions
- source feature IDs, feature-run IDs and feature-manifest IDs when the component is
  feature-derived
- an optional calculation-lineage graph ID

This is a new internal contract generation. Existing historical v1 catalog objects remain
historical evidence; they are not silently reinterpreted as v2 and they do not acquire the
new semantic guarantees retroactively.

## Feature-derived continuous states

Market and Company state are continuous feature-derived representations. Their components
must therefore carry source feature ID, feature-run ID and feature-manifest lineage, and
must use a continuous normalization rather than `STRUCTURED` or `CATEGORICAL`. A caller
cannot satisfy the contract merely by inventing a feature name.

The current Market state still preserves the nine named dimensions: growth, inflation,
liquidity, rates, credit, volatility, trend, breadth and valuation. This contract does not
claim that every dimension already has an approved canonical builder. AMA-173 separately
maps and constructs those components; an unmapped dimension must remain explicitly
unavailable rather than being filled with an arbitrary proxy.

Company state follows the same feature-lineage rule but remains inactive for company
selection in the ETF-first release.

## Portfolio and System state

Portfolio and System state are not forced to fabricate feature lineage. Portfolio components
may retain structured account/risk values using `STRUCTURED` where appropriate and still
must cite immutable evidence IDs. System health is categorical and every System component
must declare `CATEGORICAL` normalization.

System state contains broker, data, clock, reconciliation, storage, model, and execution
health. Its aggregate operational restriction remains one of NORMAL, CAUTION, REDUCED_RISK,
NO_NEW_TRADES, or HALTED. It cannot emit an instrument direction, BUY, or SELL.

## Regime boundary during migration

The old optional EXPANSION/CONTRACTION/TRANSITION helper remains temporarily for migration
only; AMA-173/03 removes regime inference from the generic State engine. Until then the
helper refuses to interpret raw values. Growth, trend and volatility must explicitly be
unitless centered/standardized state scores (`Z_SCORE`, `DIRECTIONAL_SCORE`, or
`STANDARDIZED_COMPOSITE`) before the zero thresholds are even eligible to run. Raw realized
volatility therefore cannot be mistaken for a centered volatility state.

No regime model is approved or selected by this Phase 12 contract.

## Uncertainty and operational policy

The existing State policy still records stale age and two confidence boundaries. Low
confidence maps to REDUCED_RISK, intermediate confidence to CAUTION, and blocking/stale
components to NO_NEW_TRADES; System BLOCKED/HALTED can produce HALTED. These outputs are
retained for compatibility while AMA-173 separately audits the boundary between descriptive
State and the authority-bearing `RiskGovernor`. Their existence does not allow State or a
future regime representation to mint portfolio targets, orders, or approved risk decisions.

## Determinism and lineage

Repeating the same calculation with identical values, semantics, evidence, cutoff, policy,
parameter/formula versions and code revision produces the same immutable state identity.
Changing semantic metadata such as normalization changes that identity even when the numeric
value is unchanged.

FeatureStore already verifies source manifests against the information cutoff. State v2
preserves the feature run/manifest references instead of collapsing them to feature names.
Where a `CalculationLineageGraph` exists, a component can also cite its graph ID; the State
contract does not falsely claim that a graph exists when one has not been materialized.

## Completion criteria

- the four state engines and component contracts remain separate
- State values have explicit semantic/unit/normalization contracts
- snapshot and component PIT context agree exactly
- Market/Company components retain actual feature run and manifest lineage
- Portfolio/System are not forced to fabricate feature lineage
- System State produces operational restrictions only
- every component remains visible; there is no single opaque state score
- identical semantic inputs reproduce the same state hash
- no actual regime classifier or new trading authority is introduced by this contract
