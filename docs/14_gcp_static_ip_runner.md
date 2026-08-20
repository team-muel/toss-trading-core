# GCP Static IP Foundation Runner

## Purpose

This runner makes Foundation v0/v1 reproducible on a GCP VM with a static external IP. It is still read-only:

- no automatic orders
- no strategy signals
- no external market-data feeds
- no automatic orders; the optional systemd timer remains read-only

The VM only runs `foundation_snapshot` and `foundation_audit`.

The production-lab VM installs `deploy/systemd/toss-foundation.service` and
`deploy/systemd/toss-foundation.timer`. The timer runs the read-only v0 audit
every six hours, uses a restrictive `UMask=0077`, and uploads successful
backups to the configured private Cloud Storage bucket.

Related documents:

- `docs/15_gcp_secret_manager_and_runtime.md`
- `docs/16_cloud_monitoring_runner_health.md`

## Network Requirement

Use the VM static external IP in Toss Open API allowlist. Do not use the VM internal IP such as `10.x.x.x`.

On the VM:

```bash
curl -s https://api.ipify.org
```

The returned IP must match the static external IP registered in Toss.

## VM Bootstrap

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip util-linux
gcloud --version
git clone https://github.com/team-muel/toss-trading-core.git
cd toss-trading-core
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## Secret Manager Layout

Create secrets for the Toss runtime values:

```text
toss-client-id
toss-client-secret
toss-account-seq          # optional if single account discovery is enough
toss-api-env              # live
toss-broker-base-url      # https://openapi.tossinvest.com
```

Grant the VM service account Secret Manager access:

```bash
gcloud --version
gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
  --member="serviceAccount:${VM_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"
```

Set only secret names in the VM environment. Do not put secret values in shell history.

```bash
export GCP_PROJECT_ID="your-gcp-project-id"
export TOSS_CLIENT_ID_SECRET="toss-client-id"
export TOSS_CLIENT_SECRET_SECRET="toss-client-secret"
export TOSS_ACCOUNT_SEQ_SECRET="toss-account-seq"
export TOSS_API_ENV_SECRET="toss-api-env"
export TOSS_BROKER_BASE_URL_SECRET="toss-broker-base-url"
export FOUNDATION_LOAD_GCP_SECRETS=1
export FOUNDATION_MAX_ORDER_DETAILS=1
export FOUNDATION_TARGET_ORDER_ID=""  # v0에서는 비워 둠
export FOUNDATION_BACKUP_DIR="/var/lib/toss-trading/backups"
export FOUNDATION_LOCK_PATH="/var/lock/toss-trading-foundation.lock"
# Optional:
# export FOUNDATION_GCS_BACKUP_URI="gs://your-foundation-backup-bucket/foundation"
```

The runner sources `scripts/load_gcp_secrets.sh` when `FOUNDATION_LOAD_GCP_SECRETS=1`.
The runner defaults to one order detail call per snapshot. For v1,
`FOUNDATION_TARGET_ORDER_ID` must contain the order ID captured while the manual
order was OPEN; the runner passes it as `--target-order-id`.
After a successful audit, the runner writes a SQLite backup locally and uploads it to Cloud Storage when `FOUNDATION_GCS_BACKUP_URI` is set.
The runner uses `flock` on `FOUNDATION_LOCK_PATH` to prevent overlapping cron or systemd timer runs.

Install the reviewed units only after replacing the project, user, paths, and
bucket values for the target VM:

```bash
sudo install -m 0644 deploy/systemd/toss-foundation.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/toss-foundation.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now toss-foundation.timer
```

## Runner Commands

Foundation v0:

```bash
export FOUNDATION_AUDIT_PROFILE="v0-empty-safe"
./scripts/run_foundation_gcp.sh
```

Foundation v1 after the captured manual Toss app order has actually filled:

```bash
export FOUNDATION_AUDIT_PROFILE="v1-funded-read-only"
export FOUNDATION_TARGET_ORDER_ID="<captured-order-id>"
export FOUNDATION_INCLUDE_CLOSED_ORDERS=1
export FOUNDATION_CLOSED_ORDER_LOOKBACK_DAYS=7
./scripts/run_foundation_gcp.sh
```

`FOUNDATION_INCLUDE_CLOSED_ORDERS` defaults to `1`. OpenAPI 1.2.14 documents
the paginated CLOSED list, so the scheduled service reads a bounded, overlapping
seven-day window. Increase the lookback only for a reviewed recovery run.

## Outputs

Default outputs:

```text
runtime/foundation_account_state.sqlite
runtime/foundation_account_state_report.txt
runtime/foundation_runner.jsonl
```

The JSONL log records runner, snapshot, audit, and Secret Manager loading events without printing secret values.

Cloud Logging/Monitoring expectations are defined in `docs/16_cloud_monitoring_runner_health.md`.

## Go/No-Go

Proceed to paper order planner only after:

```text
foundation_audit=ok
profile=v1-funded-read-only
```

If the audit fails, inspect:

- `runtime/foundation_account_state_report.txt`
- latest `source_health_snapshot`
- `raw_api_response` for Toss response shape changes

Keep the VM read-only until v1 passes.
