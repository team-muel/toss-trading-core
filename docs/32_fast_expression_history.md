# Fast Expression DSL and historical semantics

`alpha_management` accepts a deliberately small Fast Expression language for
research.  It compiles expressions into a typed AST, validates operators and
datafields before evaluation, and assigns a stable SHA-256 identity to the
canonical expression.  Python-callable `Alpha` expressions remain supported.

The grammar frontend was rewritten for this repository after studying
Jonathan Larkin's `marketneutral/alphatools` expression grammar, licensed
under Apache License 2.0 (Copyright 2018 Jonathan Larkin).  No Zipline runtime
or KunQuant code is included.  Runtime behavior is implemented only with the
canonical operators in `alpha_management.operators` and repository-owned
point-in-time data adapters.

Repository panels align every instrument by explicit `reference_period` and
pad missing observations with `None`; list offsets are never treated as dates.
The shared period axis accepts ISO dates/datetimes, `YYYY-MM`, or `YYYY-QN`
and must be strictly chronological from oldest to newest by parsed time value.
Cross-sectional operators mask each period to its contemporaneous universe,
and group operators use that period's classification mapping.
This permits exited instruments to remain in historical lookbacks without
requiring them to be active at the resolver's current cutoff.
Time-series windows are bounded at 10,000 observations before evaluation.

## Evaluation order

1. Select the signal session by trading-session `delay`.
2. Evaluate the compiled expression using that session's explicit
   `AsOfContext` and information cutoff.
3. Apply cross-sectional winsorization, neutralization, truncation, and book
   sizing to obtain base position weights.
4. Apply linear `decay` to the resulting position weights, with the most
   recent position receiving the largest coefficient.

Delay and position-decay warm-up values are `None`; they are never silently
converted to zero.  Expression-level `ts_decay_linear` remains a separate
time-series operator and therefore runs before the cross-sectional position
transform.

Every historical result records the canonical expression and hash, settings,
effective time, source signal time, information cutoff, dataset manifest IDs,
effective and signal-universe versions, point-in-time neutralization groups,
run ID, and code revision.  This output is research-only and does not create
orders or call brokers.  When a matching forward-return panel is supplied,
the result also carries the canonical research metric bundle.
All-unavailable warm-up periods are removed, with their matching returns,
before those metrics are calculated.
Repository-backed resolvers pin their selected immutable manifest before any
field read, and forward-return panels must cover every instrument ever held.
They also derive a stable universe identity from the complete period-membership
history when no catalog version is supplied.  Every held instrument-period
must have a realized forward return before metrics can be attached.
Realized returns for held cells must also be finite.
Resolver and session construction snapshot their point-in-time classification
mappings, and the resolver requires the instrument axis to cover every
historical member. Delayed signals are
transformed on the complete signal-session universe before their weights are
projected onto the effective universe. Operator outputs must remain finite.
