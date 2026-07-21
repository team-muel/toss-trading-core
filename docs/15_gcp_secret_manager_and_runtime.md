# GCP Secret Manager And Runtime Layout

## Purpose

This document defines where the GCP runner reads secrets from, where runtime files are stored, and what must not be committed.

The runner remains read-only. Secrets are used only to call Toss Open API account-state endpoints.

## Secret Storage Model

Store secret values in GCP Secret Manager. Store only secret names in the VM environment.

Required secrets:

```text
toss-client-id
toss-client-secret
```

Optional secrets:

```text
toss-account-seq
toss-api-env
toss-broker-base-url
```

Recommended secret values:

```text
toss-api-env=live
toss-broker-base-url=https://openapi.tossinvest.com
```

## Deployed Secret State — 2026-07-21

The production-lab VM uses the dedicated service account
`toss-foundation-runner@toss-trading-core-lab.iam.gserviceaccount.com`.
The default Compute Engine service account is not attached to the VM and its
former project Editor grant has been removed.

The Toss Client ID and Client Secret were reissued after identity verification.
For both `toss-client-id` and `toss-client-secret`:

- version 2 is enabled and is the runtime version
- version 1 is disabled, not destroyed, to preserve a reversible emergency rollback
- no plaintext key is stored in the repository, VM filesystem, shell history, or documentation

After rotation, the VM completed the v0 read-only audit twice, including once
after version 1 was disabled. This proves that the runner resolves the enabled
latest version rather than depending on the retired value.

## VM Environment

Set these non-secret environment variables on the VM:

```bash
export GCP_PROJECT_ID="your-gcp-project-id"
export TOSS_CLIENT_ID_SECRET="toss-client-id"
export TOSS_CLIENT_SECRET_SECRET="toss-client-secret"
export TOSS_ACCOUNT_SEQ_SECRET="toss-account-seq"
export TOSS_API_ENV_SECRET="toss-api-env"
export TOSS_BROKER_BASE_URL_SECRET="toss-broker-base-url"
export FOUNDATION_LOAD_GCP_SECRETS=1
export FOUNDATION_MAX_ORDER_DETAILS=1
export FOUNDATION_INCLUDE_CLOSED_ORDERS=0  # explicit recovery only
export FOUNDATION_TARGET_ORDER_ID=""  # v1에서만 captured orderId 설정
```

The values above are secret names, not secret values.

## Loading Secrets

This path requires the `gcloud` CLI on the VM:

```bash
gcloud --version
```

Use:

```bash
source scripts/load_gcp_secrets.sh
```

The script exports:

```text
TOSS_CLIENT_ID
TOSS_CLIENT_SECRET
TOSS_ACCOUNT_SEQ
TOSS_API_ENV
TOSS_BROKER_BASE_URL
```

It logs only loaded/skipped env names to stderr. It does not print secret values.

`scripts/run_foundation_gcp.sh` automatically sources `scripts/load_gcp_secrets.sh` when:

```bash
export FOUNDATION_LOAD_GCP_SECRETS=1
```

## IAM Requirement

The VM service account needs Secret Manager read access:

```bash
gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
  --member="serviceAccount:${VM_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"
```

Use the narrowest service account practical for this VM. Do not use broad owner/editor roles.

## Runtime Paths

Default runtime outputs:

```text
runtime/foundation_account_state.sqlite
runtime/foundation_account_state_report.txt
runtime/foundation_runner.jsonl
runtime/backups/*.sqlite
```

Override paths with:

```bash
export FOUNDATION_DB_PATH="/var/lib/toss-trading/foundation_account_state.sqlite"
export FOUNDATION_REPORT_PATH="/var/log/toss-trading/foundation_account_state_report.txt"
export FOUNDATION_JSON_LOG_PATH="/var/log/toss-trading/foundation_runner.jsonl"
export FOUNDATION_BACKUP_DIR="/var/lib/toss-trading/backups"
export FOUNDATION_LOCK_PATH="/var/lock/toss-trading-foundation.lock"
```

For early manual validation, repository-local `runtime/` is acceptable. For persistent VM operation, prefer `/var/lib/toss-trading` for SQLite and `/var/log/toss-trading` for logs.

Optional Cloud Storage backup upload:

```bash
export FOUNDATION_GCS_BACKUP_URI="gs://your-foundation-backup-bucket/foundation"
```

If set, the runner uploads the SQLite backup after `foundation_audit` passes. The VM service account needs write access to that bucket.

The runner requires `flock` and takes a non-blocking lock at `FOUNDATION_LOCK_PATH`. If another run is active, it writes `foundation_runner_lock_busy` and exits without starting another API polling run.

The runner sets `umask 077`. New databases, backups, reports, locks, and logs
are therefore readable only by the service user. The systemd service also sets
`UMask=0077` as a second enforcement layer.

Install runtime dependencies from `requirements.lock` and CI/test dependencies
from `requirements-dev.lock` so that a reviewed commit resolves the same
dependency versions during later deployments.

## Git Safety

Never commit:

- `.env`
- `.env.*`
- `runtime/`
- SQLite databases
- JSONL logs
- Toss access tokens
- raw account numbers
- raw account identifiers from API response bodies

The adapter redacts OAuth access tokens, account numbers, and account identifiers before storing raw response bodies. Normalized tables may retain `account_seq` only as an internal reconciliation key.

## Success Criteria

Foundation v0:

```bash
export FOUNDATION_AUDIT_PROFILE=v0-empty-safe
./scripts/run_foundation_gcp.sh
```

Expected:

```text
foundation_snapshot=ok
foundation_audit=ok
profile=v0-empty-safe
```

Foundation v1 after the captured manual tiny order is filled:

```bash
export FOUNDATION_AUDIT_PROFILE=v1-funded-read-only
export FOUNDATION_TARGET_ORDER_ID="<captured-order-id>"
./scripts/run_foundation_gcp.sh
```

Expected:

```text
foundation_audit=ok
profile=v1-funded-read-only
```

The 2026-07-21 production-lab v1 run passed using an exact order ID recovered
from a bounded CLOSED-order query. The run stored order detail, execution and
delta, actual commission, settlement, and sellable-quantity evidence, then
uploaded the v1 SQLite backup to the private bucket. Keep CLOSED lookup disabled
for the normal scheduled v0 service.

## Restore Drill — 2026-07-21

The v1 GCS backup was restored without touching the live database:

```text
/home/seoje/toss-trading/restore-drills/20260721T100415Z/foundation_account_state.sqlite
```

Verified evidence:

- SQLite `PRAGMA integrity_check` returned `ok`
- file mode is `600` and the containing directory is `700`
- restored size is 413,696 bytes
- SHA-256 is `ff39fcc2d711784f2fdb9bf05239725eafc66ff57e02f764025fa44c0091d748`
- `foundation_audit --profile v1-funded-read-only` passed with no blockers
- target detail, filled execution, execution delta, commission, settlement,
  and sellable-quantity evidence remained readable from the restored copy

The restored copy is retained as drill evidence and is not used by the live
runner.
