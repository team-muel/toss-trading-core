# Phase 11 — Feature Store

Features are versioned, point-in-time numeric states. They are neither source datasets nor
BUY/SELL instructions. Phase 11 uses the same deterministic calculation path for replay and
current runs; only `FeatureContext.as_of` and `information_cutoff` select eligible inputs.

## Contracts

Every `FeatureDefinition` records `feature_id`, namespace, name, version, input fields,
lookback, horizon, transformation, missing policy, and quality policy. Re-registering a
feature ID with a different contract is rejected. The safe defaults are
`missing_policy=MISSING_HISTORY` and `quality_policy=REQUIRE_VALID`.

Feature snapshots also carry the AMA-32 signal-validity contract: forecast and holding
horizons, an aware `valid_until`, and an explicit decay profile. This contract is part
of the deterministic feature identity and immutable Gold payload.

Every successful `FeatureSnapshot` records `feature_run_id`, instrument ID, feature ID,
`as_of`, `information_cutoff`, Decimal value, quality status, every input manifest ID,
parameter-set ID, optional parent state ID, and code revision. It is stored as an immutable gold dataset;
its parent manifests must be VALID silver datasets available by the cutoff. The historical
universe manifest must be one of those parents and must identify the historical-universe
dataset. The gold manifest becomes available at the explicit `as_of` calculation time,
never retroactively at the earlier information cutoff. Failed computations return an in-memory
MISSING/BLOCKED/QUARANTINED snapshot and `NO_TRADE` without publishing misleading gold data.

The run ID hashes the definition, instrument, cutoff, all input values and timestamps,
parameters, input manifests, universe manifest, parent state, and code revision. Identical
inputs and parameters therefore produce the same feature and immutable manifest.

## Market features

- 1, 3, 6, and 12-month returns
- 12-1 momentum
- 20 and 60-day realized volatility
- drawdown from the lookback high
- moving-average distance and volume trend
- market breadth using the historical cross-section
- gold trend slope
- corporate-credit minus risk-free spread

## Company features

- revenue, diluted EPS, and free-cash-flow growth
- FCF margin, ROIC, and debt ratio
- revenue and earnings growth relative to sector
- accrual ratio and cash conversion
- stock-based compensation to sales
- estimate revision
- valuation multiple
- shareholder yield

Company definitions are registered now, but company selection remains inactive for the
ETF-first release until their Phase 9 histories and quality gates are decision eligible.

## Leakage controls

- Historical z-score mean and variance use observations whose event is before `as_of` and
  availability is at or before the information cutoff.
- Winsorization requires values for exactly the membership of the historical universe. A
  current universe cannot silently replace it.
- Inputs with event time after `as_of` or availability after the cutoff are quarantined,
  preventing future-quarter results from entering prior-quarter features.
- Missing lookback produces `MISSING_HISTORY`; it is never zero-filled.
- The transformation name is part of the immutable definition and must match the invoked
  function. Realtime and replay do not have separate calculation implementations.

## Completion criteria

- same definition, inputs, timestamps, parameters, lineage, and code revision produce the
  same feature run and snapshot manifest
- future information is rejected before feature publication
- insufficient history is `MISSING_HISTORY`
- no feature definition or result emits BUY/SELL
- successful snapshots preserve complete input manifest and historical-universe lineage
