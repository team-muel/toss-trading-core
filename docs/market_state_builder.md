# Canonical MarketState builder

AMA-176 adds the construction boundary between immutable PIT Features and the continuous
MarketState representation. It does **not** implement a regime model.

## Why a separate builder exists

`MarketStateEngine` validates and aggregates nine named dimensions, but historically it
accepted caller-created `StateComponent` objects. That was useful for contract tests but did
not prove that a component came from a Feature that was actually published and knowable at
the State cutoff.

`MarketStateBuilder` closes that gap. A feature-derived component is accepted only when its
`StateFeatureInput` can be independently reverified against the immutable store and the
publication still satisfies the canonical `FeatureStore` contract:

1. the spec names the exact source Feature ID and source instrument;
2. the Feature definition exists in the FeatureRegistry;
3. the content-addressed Feature-definition catalog object matches that definition;
4. the supplied publishing manifest is a VALID gold `feature-snapshot` manifest with the
   canonical Phase-11 schema;
5. the gold body exactly equals the supplied `FeatureSnapshot` plus the definition catalog ID;
6. gold parents exactly equal the sorted FeatureSnapshot source-manifest IDs;
7. every parent is VALID silver data, all parent source/license contracts agree, and at least
   one parent is the historical-universe dataset required by `FeatureStore`;
8. parent manifests were available by the Feature information cutoff;
9. gold `retrieved_at` and `available_at` equal Feature `as_of`, its provider timestamp equals
   the latest parent provider timestamp, and its request hash equals the canonical
   `identity_for_request` hash;
10. the gold publication is knowable by the MarketState information cutoff;
11. the Feature validity still covers the State `as_of`.

These checks intentionally reconstruct publication invariants instead of trusting a gold
object merely because it uses the right dataset/schema names. A manually assembled object
that resembles a FeatureStore output but has forged publication timing, request identity, or
non-silver parents is rejected.

The builder then performs only an identity transfer of the already-defined Decimal Feature
value. AMA-176 deliberately does not implement z-scores, weighted combinations, PCA,
clustering, macro composites, or any other economic transform.

## Versioned MarketState spec

`MarketStateSpec` contains exactly one `MarketStateComponentSpec` for each of:

- growth
- inflation
- liquidity
- rates
- credit
- volatility
- trend
- breadth
- valuation

A component spec is either:

- `DIRECT_FEATURE`: one exact `market.*` Feature and one exact instrument, transferred as
  RAW identity only; or
- `UNAVAILABLE`: no source Feature is claimed.

The complete spec is content-addressed in the immutable catalog. Its hash becomes the State
component `parameter_set_id` and is included in State identity. A caller may construct a
research spec, but no binding is hidden: downstream approval of a spec hash is a separate
authority concern for AMA-177/179.

## Foundation spec

The built-in `market-state-foundation@1` spec intentionally has **zero active Feature
bindings**.

Phase-0 found candidate Features for some dimensions, but it did not find a reviewed
economic contract authorizing any existing Feature to become a particular MarketState axis.
For example, `market.credit_spread` being present does not by itself authorize an identity
mapping into the `credit` State component, and the existence of 20d/60d volatility Features
does not select a canonical volatility horizon.

Therefore all nine dimensions are currently represented as:

- `value=None`
- `confidence=0`
- `quality=MISSING`
- `reason_code=UNMAPPED_COMPONENT`
- immutable MarketState-spec evidence
- no fabricated Feature lineage

This produces a deterministic, explicit, fail-closed MarketState (`NO_NEW_TRADES`) rather
than an apparently complete State built from unreviewed proxies.

## Missing approved input

A future reviewed spec may bind a component to an exact Feature. If that Feature is not
supplied at build time, the component becomes explicit unavailable state with
`SOURCE_FEATURE_UNAVAILABLE`; the builder never substitutes another Feature with a similar
name.

## Confidence semantics for direct identity

For `DIRECT_FEATURE`, component confidence is `1` only because the builder performs no
additional statistical inference beyond re-verifying a VALID Feature and copying its exact
value. This is construction confidence, not predictive confidence. Any future transform that
introduces model uncertainty requires a new reviewed formula/contract rather than reusing
this direct-identity mode.

## Authority boundary

The builder cannot:

- infer EXPANSION/CONTRACTION or another regime;
- choose risk-on/risk-off assets;
- create a Forecast;
- create a portfolio target;
- issue a RiskGovernor approval;
- create an order or broker-write authority.

The existing temporary regime helper is outside this builder and is scheduled for removal
from generic State in AMA-177. State-side operational/risk-multiplier responsibilities are
reviewed separately in AMA-179.

## Completion meaning

AMA-176 is successful even if the built-in foundation spec remains entirely unavailable.
The goal is to make absence, provenance and future reviewed bindings representable without
fabricating economic meaning. Actual component economics and regime inference are later
research/governance decisions.
