# Research → Production Bridge Contract

This contract keeps research expression semantics separate from production economic semantics.

## Authority boundary

`alpha_management.Alpha` and `HistorySimulationResult` are research artifacts. They may produce expression scores and simulated positions, but they do not produce a Forecast, expected return, target portfolio, order intent, or broker write.

The only supported bridge introduced by AMA-133 consumes the latest point-in-time **raw expression score** and emits a candidate with semantic type `SIGNAL_VALUE`. Simulated research weights are not promoted to production weights.

## Required economic context

Every bridge contract states:

- currency,
- forecast horizon,
- gross/net basis,
- cost timing.

For the research-score → Signal bridge the latter two are deliberately `NOT_A_RETURN` and `NOT_APPLICABLE`. A research score cannot be relabeled as gross return, net return, model-relative alpha, benchmark active return, or regression alpha. Forecast calibration must happen downstream under its own evidence and horizon contract.

## Decay ownership

The following are distinct contracts:

1. expression decay (`ts_decay_linear` or another expression operator),
2. research-position decay used by historical simulation,
3. forecast-validity decay used after a Forecast exists,
4. IC decay, which is diagnostic evidence only.

The bridge records expression decay as consumed when it is part of the expression. Research-position decay is observed but not consumed because the bridge uses raw scores rather than simulated weights. Forecast-validity decay therefore remains available downstream. IC decay may never be applied as a value transformation.

A stage already recorded as consumed must not be applied again without a new, separately versioned semantic contract.

## Lineage bridge

The bridge does not collapse research lineage and production calculation lineage into one authority. It records a research lineage identifier derived from:

- expression hash,
- dataset manifests,
- universe version,
- source research run,
- code revision,
- signal/effective information time,
- bridge version.

Production Signal/Forecast/portfolio/journal/replay lineage may reference that bridge identifier, while continuing to maintain its own calculation and event identities.

## Operational authority

Passing this bridge does not grant live-trading authority. A bridged Signal must still pass the production Signal/Forecast validation, portfolio policy, Risk Governor, execution policy, accounting evidence, and runtime authorization gates. Missing Toss accounting/NAV/settlement evidence remains fail-closed.
