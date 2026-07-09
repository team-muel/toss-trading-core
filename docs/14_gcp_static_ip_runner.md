# GCP Static IP Foundation Runner

## Purpose

This runner makes Foundation v0/v1 reproducible on a GCP VM with a static external IP. It is still read-only:

- no automatic orders
- no strategy signals
- no external market-data feeds
- no cron or systemd automation by default

The VM only runs `foundation_snapshot` and `foundation_audit`.

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
sudo apt install -y git python3 python3-venv python3-pip
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
```

## Runner Commands

Foundation v0:

```bash
export FOUNDATION_AUDIT_PROFILE="v0-empty-safe"
./scripts/run_foundation_gcp.sh
```

Foundation v1 after a manual tiny Toss app order has become CLOSED:

```bash
export FOUNDATION_AUDIT_PROFILE="v1-funded-read-only"
./scripts/run_foundation_gcp.sh
```

## Outputs

Default outputs:

```text
runtime/foundation_account_state.sqlite
runtime/foundation_account_state_report.txt
runtime/foundation_runner.jsonl
```

The JSONL log records runner, snapshot, audit, and Secret Manager loading events without printing secret values.

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
