# Architecture

## Scope

The system is a modular monolith with implementation contracts through Phase 16.
The active operating scope remains US-listed broad-market and cash-like ETFs. It
is long-only, unlevered, read-only, and USD-invested. Company research contracts
exist for later use, but individual-stock selection remains inactive until its
data histories, policies, validation, and promotion gates are approved.

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

## Implemented calculation layers

After immutable point-in-time data and account reconciliation, the monolith
provides versioned Feature Store snapshots; four separate State engines; required
return and expected-return models; covariance, tail, exposure and stress risk;
and six-layer target portfolio construction. These layers return evidence,
quality, uncertainty, restrictions, target weights, or target quantities. They
do not authorize or transmit orders.
