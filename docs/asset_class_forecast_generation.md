# AMA-150 — Asset-class economic forecast generation contract

## Boundary

This contract produces an asset-class **forecast total return** from immutable,
point-in-time economic inputs. It is not a pricing model, an alpha calculation,
an allocation instruction, or an order authority.

```text
PIT input repository -> approved component model -> raw component estimate
  -> uncertainty/confidence + horizon/currency/compounding validation
  -> immutable input/model lineage -> ExpectedReturnComponent
  -> GrossComponentForecast -> separately evidenced cost assembly
  -> ExpectedReturnEstimate -> governed downstream forecast consumer
```

`PRICING_BASELINE_RETURN` is not an input component and must never be copied
into `FORECAST_TOTAL_RETURN_GROSS` or `FORECAST_TOTAL_RETURN_NET`. A separate,
later model-relative-alpha assessment may compare an already generated **net**
forecast with a pricing baseline. Alpha or signal forecast values cannot be
used as economic component inputs.

## Evidence selection and fail-closed rule

The production assembler, not its caller, selects a single approved immutable
PIT record for every required input. Every selected record must carry a stable
artifact ID/hash, source, available-at time, information cutoff, currency,
compounding basis, and approved model/version. The record must be available at
or before the run cutoff and match the instrument, horizon, and currency basis.

Missing, stale, ambiguous, post-cutoff, non-finite, conflicting-currency,
conflicting-horizon, unsupported-compounding, or unapproved-model evidence
returns an explicit unavailable result. It must not produce a zero component,
fall back to a caller value, or emit an `ExpectedReturnComponent`.

The generated result records the selected artifact IDs/hashes, repository
selection identity, model/version, formula version, cutoff, raw/prior
calibration inputs, availability time, economic exposure identity, and model
authorization binding. The stable calculation ID is the SHA-256 hash of that
entire canonical artifact. Replay and production consumption accept only this
ID, reload the artifact and its exact observations, and recompute every
component and aggregate; callers cannot provide context, authorization, or
economic values to the replay boundary. The input
manifest must belong to the exact runtime through its persisted ingestion
lineage, and the component model must be active for `EXPECTED_RETURN` through
the runtime's immutable model-registry snapshot.

## Asset-class component applicability

| Asset class | Required economic components | Explicitly inapplicable |
| --- | --- | --- |
| Equity ETF | underlying growth, distribution yield, valuation reversion, factor exposure, momentum/tactical overlay | none in the initial ETF vertical |
| Bond ETF | yield/carry, roll-down, duration/key-rate, optional convexity when material, credit spread, expense drag | equity growth/valuation/factor/momentum |
| Commodity ETF, `PHYSICAL_BACKED` | spot change, carry, expense | roll yield |
| Commodity ETF, `FUTURES_BACKED` | spot change, roll yield, carry, expense | none of those structural components |
| Cash/cash-like ETF | horizon-aligned yield, expense, FX effect; liquidity/settlement drag only when material and evidenced | bond roll/duration/credit and commodity roll |

Instrument structure is immutable reference data. A generator must reject a
component not permitted by that structure; in particular, a physical-backed
commodity ETF cannot receive a futures roll-yield component.

## Numerical semantics

All component values are Decimal total-return contributions expressed in the
same reporting currency, currency basis, forecast horizon, and approved
compounding convention. A model must explicitly convert annualized inputs to
the forecast horizon before aggregation. It cannot add rates with incompatible
compounding or horizons.

Each component preserves raw estimate, uncertainty, confidence, and a
documented shrink/calibration result. Shrinkage requires an approved prior and
model version; a test literal or manual production estimate is not a prior.
Structural carry and tactical overlay are separately identified so the same
economic exposure cannot be counted twice.

## Initial acceptance

1. For SPY, QQQ, VTV, TLT, GLD, and SGOV, the assembler either produces every
   applicable component from persisted PIT evidence or returns an explicit
   unavailable result with the missing/invalid evidence reason.
2. No public production entry point accepts a caller-authored component point
   estimate, input ID, pricing baseline, or alpha as a substitute for repository
   selection.
3. A TLT golden calculation reconciles carry, roll-down, duration/key-rate,
   optional convexity policy, credit spread, and expense through the declared
   horizon/compounding conversion.
4. A physical GLD input rejects roll yield; a futures-backed commodity permits
   it only with independent persisted roll evidence.
5. The sum of generated components exactly reconciles to a typed
   `GrossComponentForecast`; separately evidenced costs are the only path to
   an `ExpectedReturnEstimate.net_expected_return`. The component assembler
   cannot synthesize zero costs or emit a net forecast.
6. Mutation, cross-run mixing, post-cutoff selection, stale evidence, manual
   values, pricing-baseline substitution, alpha substitution, and component
   double-counting have negative tests.

This contract does not provide accounting truth, canonical production evidence,
Gate D2 PASS, M5, or live-trading authority.
