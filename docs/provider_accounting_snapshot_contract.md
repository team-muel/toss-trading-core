# Provider accounting snapshot contract

`ProviderAccountingSnapshotRepository` is the only new path that can create a
v2 accounting NAV snapshot from a provider statement. It accepts only three
identifiers: an existing `runtime_run_id`, a previously persisted provider
contract, and a verified immutable raw-response ID. It has no parameters for
cash, NAV, holdings, settlement amounts, or FX.

Before use, the version-controlled `config/provider_accounting_contracts.json`
registry must contain the exact provider, statement endpoint, provider schema,
approval endpoint/schema, normalized statement mapping, approver, and effective
interval. `ProviderAccountingContractRepository` then records that exact entry
and a hash-verified approval response, using the evidence store's UTC clock
rather than a caller-supplied timestamp. Contracts and produced snapshots are
append-only. A snapshot is bound to the exact runtime, provider contract, raw
response ID/hash, request/receipt timestamps, and observed-at timestamp; it can
be replayed from the persisted evidence. The recorded contract and its approval
response must both predate the statement request and be effective at that time,
so evidence cannot be retroactively approved for a historical runtime.
The content-addressed contract ID, contract payload, table approval ID, and
verified approval response must all agree; an inconsistent append-only row is
rejected rather than treated as recoverable evidence.
The approval response is a normalized immutable artifact that attests to the
provider, endpoint, schemas, contract version, effective interval, approver,
and canonical registry digest. Creation and replay both verify that artifact;
a response from the right endpoint alone cannot authorize a contract.
The registry is loaded only from the repository checkout or installed package
data, never from a same-relative path in the process working directory. The
exact approved rule and registry hash are embedded in the immutable contract
payload; replay therefore validates the original authorization, not a later
edited registry.

The provider response must contain a complete normalized statement:

- reporting-currency reported NAV;
- explicit settled cash, unsettled cash, settlement receivable, and settlement
  payable components per currency, each linked to its counted/included NAV
  component;
- itemized holdings with `quantity × marketPrice = marketValue`, reconciled to
  securities components;
- explicit FX rates that agree with every non-reporting-currency component and
  valuation; and
- per-currency `brokerBuyingPower` as a separate constraint.

Every cash/settlement component must be referenced exactly once by the explicit
cash-state breakdown. Every securities component must be a top-level complete
holdings valuation. This v1 provider-statement contract rejects every
`includedInField`: without an explicit non-overlapping aggregate contract, an
included cash, settlement, or security value could be silently hidden or counted
twice. A future provider contract may add that capability only with a reviewed
formula and dedicated evidence fields.

`brokerBuyingPower` is rejected as a NAV component. It remains outside
`nav_basis`; identical numeric values do not make it cash or NAV. The
repository also rejects absent raw evidence, hash failures, unrecorded or
changed contracts, account/source/schema mismatches, observations after the
raw receipt or runtime cut-off, incomplete state fields, double-countable NAV
components, and any holdings/FX/NAV mismatch.

The approved Toss REST API currently provides no accounting statement endpoint
and no field-inclusion contract for settled cash, receivable/payable, or full
NAV. `toss` (with any letter case) is consequently not an eligible provider contract here; Toss
holdings and `cashBuyingPower` cannot be relabelled as this statement. Until a
separately reviewed accounting provider contract and a persisted response are
available, materialization fails closed. This does not change Gate D2, #77,
or #79. The committed registry currently has no entries, deliberately leaving
all materialization blocked rather than shipping a synthetic test provider.
