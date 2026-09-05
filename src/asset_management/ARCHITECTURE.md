# Modular-monolith dependency rule

Dependencies in the investment core point inward only:

```text
execution
  -> decisions
    -> portfolio
      -> pricing / expectations / risk
        -> states
          -> features
            -> broker / account / ledger / data / quality
              -> time / config / reference
                -> domain
```

A module may depend on its own level or any level above it in this diagram. It
must not import a lower level. In particular, features cannot create orders and
execution cannot call pricing or feature computation to manufacture a new
decision. Execution consumes an already journaled and governed order intent.

`replay`, `validation`, `orchestration`, `monitoring`, `reporting`, and `cli` are
outer application-shell modules. They may compose core modules, while core
modules must never import them.

Imports from the legacy `toss_trading` package are permitted only inside
anti-corruption adapters while the verified Toss foundation is migrated. They
must not leak into domain models or investment logic.

## Runtime gate order

Import direction and runtime approval order are separate controls. Every
decision run must produce immutable evidence in exactly this order:

```text
investment policy
  -> account truth
    -> time truth
      -> data truth
        -> financial calculation
          -> target portfolio
            -> risk control
              -> order
```

No stage may infer, repair, or replace an earlier stage. A missing, stale,
conflicting, or unreconciled prerequisite stops the run. In particular, an
order is not an input to risk control, a target portfolio is not adjusted in
the execution layer, and financial calculations cannot manufacture missing
data. Each gate records an evidence identifier and content hash under the same
runtime run identifier.
