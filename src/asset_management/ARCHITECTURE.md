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

A module may depend on its own level or an inward level below it in this diagram.
It must not import an outward level above it. In particular, features cannot create orders and
execution cannot call pricing or feature computation to manufacture a new
decision. Execution consumes an already journaled and governed order intent.

`replay`, `validation`, `orchestration`, `monitoring`, `reporting`, and `cli` are
outer application-shell modules. They may compose core modules, while core
modules must never import them.

`governance` and the root `recovery.py` contract module join the `features` level: immutable registry, recovery and mandate contracts
consume validated data but do not compose calculations or decisions.
`calculations` and `signals` join the `pricing / expectations / risk` level.
These explicit placements extend the diagram to all current core packages.

CI runs `python scripts/check_layer_dependencies.py` before dependency installation.
It parses imports without loading application code, resolves relative imports,
checks imports in conditional/type-checking blocks, and rejects unknown layers
and ambiguous package-root imports. Execution cannot import feature/state,
pricing/expectation/risk, calculation/signal or portfolio computation modules.
The existing `check_legacy_boundary.py` separately enforces legacy and
dynamic-import restrictions; this check does not duplicate those trust rules.
Import checks do not prove runtime evidence, policy approval or trading authority.
CI also checks all `domain`, `time`, and `config` files with pinned Ruff
correctness rules E9/F63/F7/F82 and strict mypy. These 22 foundation files
are the explicit initial static-analysis scope; other packages remain covered
by layer/legacy checks and runtime tests, without a repository-wide typing claim.
Mypy follows imports normally; missing imports are not suppressed and imported
modules are not replaced with Any. PyYAML stubs are a development dependency.
Run `python -m mypy` and the Ruff command from the CI workflow locally.
Static casts express postconditions already enforced by runtime validation;
they do not relax validation, change economic formulas or promote evidence.
The layer checker also rejects direct `alpha_management` imports from every
core layer; only outer shell integration may consume sibling research results.

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
