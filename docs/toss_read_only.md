# Toss read-only layer

Version: `toss-read-v1`

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
