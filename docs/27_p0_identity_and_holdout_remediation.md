# P0 identity and prospective-holdout remediation

## Current verified state on 2026-08-08

- `personal-agent-vm` is running with
  `toss-foundation-runner@toss-trading-core-lab.iam.gserviceaccount.com`.
- Foundation `v0-empty-safe` completed at 2026-08-08 13:12:05 KST with a
  successful snapshot, audit, local backup, and GCS backup.
- Daily research completed from the holdout start through 2026-08-07 except for
  the first 2026-08-08 attempt, which timed out while reading Tiingo.
- A bounded recovery run, `daily-20260808T050927Z-36a60066a485`, completed and
  recovered Tiingo data through 2026-08-07 within one calendar day.
- The baseline prospective start remains 2026-08-03. Performance was not used
  to make this amendment.

## Enforced prospective rules

1. Tiingo total-return observations require an append-only collection ledger.
   A collection row is eligible only after the same run writes a completion
   marker following QA, backup, reporting, and optional delivery steps.
2. Each market date must be collected no later than three calendar days after
   that date. A later backfill marks the holdout `invalid_data_gap`.
3. While collecting or invalid, metrics, benchmark metrics, equity curves,
   rebalances, and walk-forward details after the holdout start are sealed.
4. Only the first 126 verified trading dates can become headline results.
5. An invalid interval is never silently removed; it must be recorded in the
   immutable validation protocol.

## Research identity target

The target runtime is a separate `personal-research-agent-vm` using only
`toss-research-runner@toss-trading-core-lab.iam.gserviceaccount.com`.

Allowed access:

- `toss-research-client-id` and `toss-research-client-secret`
- Tiingo, FRED, SEC, and research Gmail secrets after their individual gates
- create-only research GCS writes
- research BigQuery dataset writes
- Vertex AI, logging, and monitoring

Forbidden access:

- Foundation `toss-client-id`, `toss-client-secret`, account sequence, API
  environment, and broker URL secrets
- Foundation backup bucket
- live-order policy or deployment authority

## Migration gate

1. Create the research service account and a separate research VM.
2. Register a distinct read-only Toss research application/static IP if Toss
   cross-checking remains enabled.
3. Run `scripts/check_research_identity_gcp.sh`; it must return
   `research_identity=ok`.
4. Complete seven consecutive daily runs and one weekly run on the new VM.
5. Disable research timers on `personal-agent-vm` only after the evidence in
   step 4 exists. Foundation timers remain on the original VM.

The current shared VM is therefore a documented P0 migration state, not the
approved live architecture.
