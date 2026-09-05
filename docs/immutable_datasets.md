# Immutable datasets (phase 8)

`asset_management.data.immutable.ProviderDatasetAdapter` is the shared ingestion
boundary for JSON provider datasets. `ImmutableDatasetStore` stores content in
`bronze/`, `silver/`, and `gold/`; `catalog/manifests/`, `catalog/schemas/`,
`catalog/instrument-mappings/`, and `catalog/source-health/` preserve metadata.
The existing SQLite broker-response and ledger manifests remain compatible and
separate; their IDs cannot be substituted for content-addressed catalog IDs.

Every manifest records `manifest_id`, `source`, `dataset`, `schema_version`,
`retrieved_at`, `available_at`, `content_sha256`, `row_count`, `license_tag`,
`code_revision`, `parent_manifest_ids`, and `quality_status`. Additional fields
are `layer`, `provider_timestamp`, and `request_hash`. Times are aware UTC ISO
strings. Availability cannot precede receipt; provider time cannot follow receipt.
License tags use the mandatory form
`purpose=<scope>;redistribution=<forbidden|permitted>;retention=<period>`.
Missing, duplicate, or unknown fields are rejected. A tag records permissions; it
does not grant permission to distribute.

The adapter first redacts and publishes canonical provider JSON, hashes the stored
bytes, and publishes the bronze manifest. Only then does it validate the provider
envelope, normalize, validate required scalar fields, map provider instrument IDs,
and publish silver. Raw and normalized schema definitions are stored by hash. Bronze
quality is RAW; successful silver is VALID. Failed normalization, mapping, schema,
or HTTP status returns NO_TRADE with a reason code and an immutable BLOCKED health
event. Exception messages are never persisted. Invalid metadata fails closed before
publication. Storage failures that prevent health recording propagate to the caller.

Transport code must supply decoded JSON, trustworthy timestamps, HTTP status,
endpoint, HTTP method, request parameters, and **all active credential values**
through `ImmutableDatasetStore(..., secrets=(...), credentials_classified=True)`.
The explicit classification flag is required even for a public source with an empty
credential list. Method and endpoint are mandatory parts of `request_hash`. This list
removes echoed credentials from arbitrary text as well as structured sensitive
fields. Credential-like Bearer, JWT, and API-key strings that survive redaction are
rejected before publication. Do not pass unclassified binary responses or undisclosed credentials.
The retained original is redacted canonical JSON, not the unredacted HTTP bytes.
JSON key order does not affect its SHA-256. Raw data must never contain secrets.

Normalization callbacks must be inspectable Python functions. Their source hash,
explicit `git:<7-40 lowercase hex>` code revision (covering dependencies and configuration), and instrument
mapping hash identify the normalized version. Schema definitions are stored by
hash and included in schema_version. Changed normalizer source creates a new
manifest even when output bytes are identical. Closures and external dependencies
require a new explicit code_revision when changed. Do not reuse a revision label
for changed dependencies. Financial quantities should use decimal strings.

`row_count` is computed from a top-level array or a single recognized provider
collection (`result`, or `items`, `orders`, `rows`, or `data` inside an object).
Ambiguous envelopes fail closed rather than recording a guessed count.

Publication uses same-filesystem atomic hard links and never replaces a destination.
An existing object's bytes must match exactly. Repeated identical events deduplicate;
new receipt times, revisions, or content create new manifests. Partial publication
can leave an unreferenced blob, which is never considered a valid dataset. The
filesystem must support hard links. This is application-level immutability, not
protection against an administrator changing files. Readers verify every manifest,
blob hash, and ancestor, rejecting missing or modified history.

Derived artifacts require existing parents from a lower layer and cannot become
available before them. Source and license must match parents; cross-provider gold
aggregation needs an explicit combined licensing contract and is rejected here.
Use `store.read(silver_manifest_id)` and its `parent_manifest_ids` to trace rows
back to bronze. `store.write(..., layer="gold", parent_manifest_ids=(silver_id,))`
supports validated features, states, and verification results. The caller owns gold
validation; the store enforces integrity and lineage. This repository capability
does not activate network fetching, the trading pipeline, or real orders.

`asset_management.data.adapters.toss.TossDatasetAdapter` is the concrete bridge to
`TossReadOnlyAdapter`. It invokes only a named read `get_*` operation, requires the
upstream append-only raw response ID, and then runs the immutable provider contract.
Its constructor verifies that Toss client ID, client secret, and account sequence
are registered for redaction. The account-truth broker adapter retains its existing
SQLite raw ledger because it is an account-truth boundary rather than a market,
macro, or company dataset adapter.
