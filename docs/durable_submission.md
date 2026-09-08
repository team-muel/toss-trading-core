# Durable submission and ambiguous recovery (AMA-115)

SubmissionJournal owns a dedicated SQLite connection with synchronous FULL.
submit_once commits SUBMITTING, client identity, account, payload and SHA-256
before invoking the injected transport. It never imports a Toss write adapter.
Transport authorization and account/risk gates are prerequisites of the caller;
this protocol is not authorization to enable live orders.

Same-client same-payload retries return stored state without transmission.
The economic key also prevents changed quote IDs from resubmitting the same
account/run/target/instrument/side. Conflicting payloads are rejected. Any
unresolved submission blocks new submissions for that account.

Timeout or exceptions produce UNKNOWN_BROKER_STATE. Abrupt process termination
leaves SUBMITTING, which recovery treats as equally ambiguous. A crash before
commit permits a first attempt; a crash after commit never permits a blind retry,
even if transport was not reached. ACK responses are hashed for audit and remain
unresolved until read-only lookup and reconciliation prove broker/account truth.

Lookup must match client, account, instrument, side, quantity and submission window,
and return broker order, raw response, account snapshot and reconciliation IDs.
Partial/full fills additionally require verified fill/ledger reconciliation.
Absent, unavailable or conflicting lookup results remain UNKNOWN. CANCEL/REPLACE
must call mark_ambiguous before transport and reconcile afterward; there is no
automatic replacement. Recovery and status records are append-only audited.

Producer adapters must verify those evidence references against their stores.
This module does not simulate fills or perform reconciliation itself; AMA-63/116
provide subsequent paper persistence and atomic fill/ledger work.

## AMA-133 adversarial review: recovery serialization

An ambiguity marker is monotone and is committed under `BEGIN IMMEDIATE` before
transport. Recovery uses the maximum marker, including markers from older
journals that may have been appended out of chronological order. It captures
an audit sequence before lookup and compares that sequence again under a write
transaction before publishing recovery. Matching state strings alone are not
sufficient: a second CANCEL may leave the state UNKNOWN while invalidating the
first lookup, even when both markers have the same timestamp.

The raw request must have started at or after the ambiguity boundary; late
receipt of a pre-cancel request is not post-cancel evidence. Raw receipt,
order-state observation, account-snapshot observation, and reconciliation
completion must also satisfy the boundary and completion ordering. The test
fixture carries the same observation columns as the production ledger schema.
