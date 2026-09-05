# Architecture

## Scope

The first release is a modular monolith for US-listed broad-market and cash-like
ETFs. It is long-only, unlevered, and USD-invested. Its objective is operational
correctness: point-in-time data, target weights, risk gates, simulated execution,
and exact reconciliation. Individual-stock research is outside this phase.

## Mandatory runtime order

Investment policy -> account truth -> time truth -> ledger replay/account
reconciliation -> data truth -> financial calculation -> target portfolio -> risk
control -> order -> fill -> ledger -> post-trade reconciliation. Every stage records
immutable evidence. Missing evidence stops the run; a later stage may never repair an
earlier one.

Pipeline progress is stored in `am_pipeline_stage_evidence`. A stage is accepted only
when its identifier and content hash resolve to the corresponding immutable artifact
under the same runtime run. The database enforces contiguous ordering, and an order
intent requires the matching persisted risk-control stage; in-memory strings are not
authorization evidence.

The application is `src/asset_management`. The verified legacy Toss client is
temporarily isolated behind `broker/toss_read.py`; investment logic cannot import
it directly. Service extraction is prohibited until the monolith is stable.

See `src/asset_management/ARCHITECTURE.md` for compile-time dependency rules and
`docs/adr/` for binding decisions.
