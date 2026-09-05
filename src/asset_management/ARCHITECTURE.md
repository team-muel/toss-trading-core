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

## Alpha research boundary

`alpha_management` is a sibling research package rather than a child of the
investment core. It may depend read-only on `asset_management.time`,
`asset_management.reference`, and validated `asset_management.data` interfaces.
The dependency is one-way: core `asset_management` modules must not import
`alpha_management`.

The alpha package owns expression syntax, BRAIN-style operators, research
simulation, and evaluation only. It does not call providers or brokers, mutate
account state, create order intents, or bypass portfolio/risk policy. Any later
handoff of a validated alpha result into a target-portfolio proposal must occur
through an explicit outer integration boundary and then follow the normal
runtime gates below.

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
