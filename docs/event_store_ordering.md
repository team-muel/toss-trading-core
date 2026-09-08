# Event envelopes and deterministic ordering (AMA-65)

The replay EventStore uses a dedicated SQLite database, FULL synchronous writes,
immutable rows and SHA-256 verified canonical JSON envelopes. It stores all
fourteen market, macro, filing, estimate, corporate, account, order, source-health
and policy event types enumerated in `EventType`. Payloads must be nonempty JSON
objects; duplicate keys, nonfinite values and unknown event types are rejected.

Producers supply a durable sequence unique within their source stream. A source
identifier must include the stream/partition identity when sequences are partitioned.
The store never generates sequence numbers from ingestion order or wall time.
Identical redelivery is idempotent. Different content at the same source sequence
fails with EVENT_SEQUENCE_CONFLICT. Revisions are new events, not updates.

All timestamps normalize to UTC and satisfy event_time <= received_at <= available_at.
Reading at a cutoff excludes events not yet available and verifies stored hashes.
Consumers pass the verified envelopes to `order_events`, which orders by
available_at, received_at, configured source priority (smaller first), then sequence.
Unknown sources, duplicate sequences and ties in all four ordering fields fail
closed. No insertion-order or arbitrary event-ID tiebreaker is used. Callers must
persist their source-priority configuration alongside the replay specification.

This layer stores and orders evidence; the end-to-end replay engine and producer
adapters are separate work. Opening a store does not access brokers or submit orders.
