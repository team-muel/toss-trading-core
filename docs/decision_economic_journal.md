# AMA-60 — Decision Journal, Economic Semantics, and Decision Quality

`EconomicDecisionRecord` is the versioned, immutable portfolio-decision record. It binds a
content-addressed `decision_id` to the run, `as_of`, information cutoff, assessment horizon,
mandate and benchmark versions, reporting currency, risk-budget and risk-aversion policy versions, four state
snapshots (market, company, portfolio, system), pricing/forecast/risk/target/decision/calculation
lineage, risk snapshot and contribution type, all three target stages, risk decision and reason
codes, and policy/parameter/model/code versions.

The seven return fields are `ReturnMetric` values with a distinct `semantic_type`: pricing
baseline return; gross and net forecast total return; model-relative alpha; expected
benchmark-active return; realized active return; and regression alpha. Each metric records its
status, currency basis, decision horizon, unit, formula version, and model version. The contract
does not have a generic `alpha` field. Gross and net forecasts cannot be reversed, and a
benchmark-relative or mixed mandate requires the expected active-return metric together with the
mandated benchmark version. `EconomicDecisionJournal` requires the AMA-124 mandate registry and
resolves the record's mandate key, objective, reporting currency, benchmark key, and both versions
at the recorded `as_of`; a copied or stale version is rejected. An absolute-wealth mandate records
the expected active-return metric as `NOT_APPLICABLE`.

The initial record can only mark realized active return and regression alpha `NOT_MATURED`.
`DecisionOutcomeEvent` later stores those metrics with the same semantics and content-hash
binding. It may be appended only at or after the recorded assessment horizon; early, mismatched,
or altered outcome data is rejected. Decision quality is then one of
`GOOD_DECISION_GOOD_OUTCOME`, `GOOD_DECISION_BAD_OUTCOME`,
`BAD_DECISION_GOOD_OUTCOME`, or `BAD_DECISION_BAD_OUTCOME`, keeping process quality separate from
outcome quality. Before the horizon it is `NOT_MATURED`; after the horizon with no recorded
outcome it is `PENDING_OUTCOME`.

`EconomicDecisionJournal` writes decision and outcome events as append-only JSONL. New decisions
use `decision-economic-journal@2`. All seven metrics must share currency basis and horizon.
For a pricing model that does not apply, the baseline and model-relative alpha both carry
`NOT_APPLICABLE`, null values and an empty pricing lineage. Available pricing still requires
its lineage. Missing forecasts, premature pricing status and fabricated model-relative alpha
are rejected. Applicability must come from upstream model/asset scope evidence; this journal
does not infer it from holdings or authorize models.

Explicit `schema_version="decision-economic-journal@1"` preserves legacy construction and
replay, including its mandatory baseline and exact original IDs/hashes. Reading never upgrades
stored rows. Outcome events retain their unchanged v1 contract and bind to either decision
version by its exact ID/hash. Unknown versions and tampered hashes are rejected.

The serialized contracts are [decision_economic_journal.schema.json](../schemas/decision_economic_journal.schema.json)
and [decision_outcome.schema.json](../schemas/decision_outcome.schema.json). This journal records
evidence only and does not create an order or enable live trading.
