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
