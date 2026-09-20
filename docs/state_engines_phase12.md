# Phase 12 — Four State Engines

Phase 12 keeps market, company, portfolio, and system state as separate contracts. A state
is a versioned snapshot of components with uncertainty and lineage. It is not an order or
investment signal.

## State snapshot v3

`state-snapshot-v3` strengthens the State boundary so a future regime model cannot infer
meaning from an untyped number. Every snapshot records both `as_of` and
`information_cutoff`; every component must use exactly the same temporal context as the
snapshot. The state identity is a deterministic SHA-256 over the complete component
semantics and lineage, the state policy, times, and code revision. AMA-177 removed regime
derivation from generic State identity, and AMA-180 removes the legacy `regime_label`
field from the canonical snapshot entirely. Historical v2 artifacts remain historical data;
canonical writers emit v3 and do not reproduce the retired label.

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
- zero or more typed `StateFeatureInput` references
- an optional calculation-lineage graph ID
- an optional machine-readable `reason_code`

A `StateFeatureInput` keeps one complete `FeatureSnapshot` and one claimed publishing gold
manifest ID in the same typed reference. The feature ID, instrument, feature-run ID, feature
cutoff, source data manifests, parameter set, code revision and validity therefore cannot be
detached from that manifest ID by sorting unrelated arrays. Snapshot-level
feature/run/manifest/data-manifest arrays are derived summaries; the component-level typed
reference is authoritative for identity.

The reference contract alone does **not** prove that the manifest actually contains that
FeatureSnapshot. Store-backed content verification belongs to the canonical
Feature-to-MarketState builder in AMA-173/02, following the existing `SignalStore` pattern.
Until then this layer preserves the evidence coordinates without overstating source truth.

This is a new internal contract generation. Existing historical v1 catalog objects remain
historical evidence; they are not silently reinterpreted as v2 and they do not acquire the
new semantic guarantees retroactively. The later `reason_code` field is an optional v2
extension so the earliest v2 payloads produced by AMA-175 remain schema-compatible; new
unavailable components, however, are required by the runtime contract to supply it.

## Feature-derived continuous states

Market and Company state are continuous feature-derived representations. A component that
claims an available value must carry at least one `StateFeatureInput` and must use a
continuous normalization rather than `STRUCTURED` or `CATEGORICAL`. Feature snapshots that
are expired or known only after the State cutoff are rejected. Every referenced
feature-manifest ID must also appear in the component evidence set, and a component may
degrade source quality but may not claim a better quality status than its worst feature
input. A caller cannot satisfy the contract merely by inventing a feature name.

A component may instead be **explicitly unavailable** without fabricating feature lineage.
For Market/Company this is allowed only when all of the following are true:

- `value` is `None`
- `confidence` is exactly zero
- quality is one of `MISSING`, `PRIMARY_PENDING`, `BLOCKED`, or `QUARANTINED`
- `reason_code` is present, for example `UNMAPPED_COMPONENT`
- immutable evidence IDs are still present
- there are no fake `StateFeatureInput` references

Such a component makes aggregate quality blocking and therefore produces `NO_NEW_TRADES`
under the existing State policy. `STALE` and `CONFLICT` are not used as feature-less
shortcuts: those statuses imply evidence that should remain attached to the component.

The current Market state still preserves the nine named dimensions: growth, inflation,
liquidity, rates, credit, volatility, trend, breadth and valuation. This contract does not
claim that every dimension already has an approved canonical builder. AMA-173 separately
maps and constructs those components; an unmapped dimension remains explicit unavailable
state rather than being filled with an arbitrary proxy.

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
helper refuses to interpret raw values and fails closed when growth, trend, or volatility is
unavailable. Available growth, trend and volatility must explicitly be unitless
centered/standardized state scores (`Z_SCORE`, `DIRECTIONAL_SCORE`, or
`STANDARDIZED_COMPOSITE`) before the zero thresholds are even eligible to run. Raw realized
volatility therefore cannot be mistaken for a centered volatility state.

No regime model is approved or selected by this Phase 12 contract.

## Uncertainty and operational policy

The existing State policy still records stale age and two confidence boundaries. Low
confidence maps to REDUCED_RISK, intermediate confidence to CAUTION, and blocking/stale
components to NO_NEW_TRADES; System BLOCKED/HALTED can produce HALTED. The accompanying
`risk_multiplier` is retained as a **state-local operational/quality scalar**. Repository
inspection for AMA-180 found no consumer outside `asset_management.states`; it is not a
RiskGovernor approval, portfolio multiplier, target-weight authority, or order authority.
AMA-179 remains the only reviewed bridge from regime uncertainty into the existing
authority-bearing `RiskGovernor`.

## Determinism and lineage

Repeating the same calculation with identical values, semantics, evidence, cutoff, policy,
parameter/formula versions and code revision produces the same immutable state identity.
Changing semantic metadata or an unavailable `reason_code` changes that identity even when
other fields are unchanged.

FeatureStore already verifies its own source manifests against the information cutoff. State
v3 retains the referenced FeatureSnapshot, its claimed publishing manifest, and the
FeatureSnapshot's source-manifest coordinates instead of collapsing provenance to a feature
name. AMA-173/02 verifies those references against the immutable store while constructing
canonical MarketState components. Where a `CalculationLineageGraph` exists, a component can
also cite its graph ID and must include that graph ID in its evidence set; the State contract
does not falsely claim that a graph exists when one has not been materialized.

## Completion criteria

- the four state engines and component contracts remain separate
- State values have explicit semantic/unit/normalization contracts
- snapshot and component PIT context agree exactly
- available Market/Company components retain atomically grouped FeatureSnapshot + manifest coordinates
- unavailable Market/Company components are explicit, zero-confidence, blocking and never fabricate feature lineage
- expired/future FeatureSnapshot references fail closed
- component quality cannot silently upgrade worse source-feature quality
- Portfolio/System are not forced to fabricate feature lineage
- System State produces operational restrictions only
- every component remains visible; there is no single opaque state score
- identical semantic inputs reproduce the same state hash
- no actual regime classifier or new trading authority is introduced by this contract


## AMA-177 regime inference boundary

Generic State construction no longer derives a market regime. AMA-180 removes
the legacy `regime_label` field from the canonical State schema and payload.
Historical v2 snapshots may still contain that field, but replay/migration code must
treat it as historical data rather than a writable canonical contract.

Future regime inference uses a separate typed contract:

- `RegimeModelSpec` binds the model/version, source State type, consumed State
  components, opaque latent-state IDs, parameter set, and purpose.
- `RegimeSnapshot` binds one source State identity, model-spec identity,
  `as_of`, `information_cutoff`, probability simplex, entropy, confidence,
  evidence lineage, and code revision.
- `FILTERED_CAUSAL` means the output is intended to be knowable from
  information available at the snapshot cutoff.
- `SMOOTHED_RETROSPECTIVE` means the output may use later observations and is
  research evidence only. The two semantics have different content identities.
- Latent state IDs are intentionally opaque. Economic names such as
  `EXPANSION` or `CONTRACTION` are not canonical model outputs at this
  boundary.
- The regime payload contains no forecast, expected return, risk multiplier,
  target weight, order intent, or broker-write authority.

The model may later be registered under the existing
`ModelScope.STATE_INFERENCE`, but registry authorization does not turn the
RegimeSnapshot itself into trading authority. Forecast/Risk consumer policy is
owned separately by AMA-179.

`alpha_management` Fast Expression is not a required runtime dependency of
this contract. Research-language outputs may enter only after explicit
materialization into canonical Feature/State evidence through an outer bridge.
