# Toss read-only layer

Version: `toss-read-v2`

The broker read port exposes only the complete account-truth collection operation.
Partial holdings/open-order/buying-power snapshots are not valid account truth and
cannot replace the full account, order, execution, fee, sellable-quantity, calendar,
and instrument-reference collection.

The enabled boundary is OAuth token acquisition plus GET operations for accounts,
holdings, open and closed orders, order detail and executions, buying power,
sellable quantity, commissions, supported reference/market data, and KR/US market
calendars. Order creation, modification, cancellation, and conditional-order APIs
remain disabled.

Every HTTP response, including non-2xx responses, is redacted and appended before
normalization. Stored evidence includes endpoint, method, canonical request and
response hashes, status, body, request/receive timestamps, internal account key,
schema version, and non-sensitive operational headers. Tokens, secrets,
Authorization/cookie headers, full account numbers, and personal data are removed
before persistence.

Required fields, enums, numeric and timestamp formats, pagination, error envelopes,
rate-limit headers, unknown fields, and timestamp semantics are contract concerns.
Unknown enum values are preserved as raw values with status UNKNOWN; they are never
coerced to a known enum and block affected order decisions.

Rate limits use a token bucket per API group. Existing order state and reconciliation
reads take precedence over new market-data reads. Under limit pressure the system
degrades new reads, never account truth.

The read adapter builds account truth only from persisted raw response identifiers.
A complete collection includes accounts, holdings, open and closed orders, order
details, currency buying power, per-position sellable quantities, commissions,
KR/US calendars, and instrument reference data. The consistency operation performs
two independent collections and blocks with `UNRECONCILED` semantics when their
non-volatile account sections disagree.

Read-only replay verifies each stored response hash and never contacts Toss. HTTP,
schema, unknown-state, and repeated-read consistency failures are written to source
health as blocking evidence. No write endpoint is reachable from this layer.
