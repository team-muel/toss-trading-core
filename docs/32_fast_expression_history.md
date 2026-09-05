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
universe version, run ID, and code revision.  This output is research-only and
does not create orders or call brokers.
