# Cloud Monitoring Runner Health

## Purpose

This document defines the minimum health checks for the GCP foundation runner.

Do not add Grafana, Metabase, or trading dashboards yet. The current goal is to know whether the read-only runner is alive, whether Toss access is healthy, and whether foundation audit passes.

## Health Sources

The runner produces three local evidence sources:

```text
runtime/foundation_account_state.sqlite
runtime/foundation_account_state_report.txt
runtime/foundation_runner.jsonl
runtime/backups/*.sqlite
```

The JSONL log is the primary VM log source. Each line is a JSON object with at least:

```json
{"ts":"...","event":"..."}
```

Stable Cloud Monitoring event contract:

```text
foundation_runner_start
foundation_runner_ok
foundation_runner_failed
foundation_snapshot_start
foundation_snapshot_ok
foundation_snapshot_failed
foundation_audit_start
foundation_audit_ok
foundation_audit_failed
gcp_secret_environment_loaded
```

Additional runner operations events:

```text
foundation_runner_backup_ok
foundation_runner_backup_upload_ok
foundation_runner_lock_busy
```

## Minimum Cloud Monitoring Policy

Send `runtime/foundation_runner.jsonl` to Cloud Logging using the Ops Agent or a small file tailer. Alert only on operational failures:

- no `foundation_runner_ok` event after a scheduled/manual run
- latest event is `foundation_runner_failed`
- latest event is `foundation_snapshot_failed`
- latest event is `foundation_audit_failed`
- expected `foundation_runner_backup_ok` event is missing after a successful audit
- `FOUNDATION_GCS_BACKUP_URI` is set but `foundation_runner_backup_upload_ok` is missing
- repeated `foundation_runner_lock_busy` events indicate cron or systemd timer overlap
- latest `source_health_snapshot.source_status` is not `ok`
- Toss error action is `register_current_ip_in_toss_openapi_allowlist`
- v1 audit fails after a manual funded validation run

## Suggested Log-Based Metrics

Create log-based counters from JSONL events:

```text
foundation_snapshot_ok_count
foundation_snapshot_failed_count
foundation_audit_ok_count
foundation_audit_failed_count
gcp_secret_environment_loaded_count
foundation_runner_backup_ok_count
foundation_runner_backup_upload_ok_count
foundation_runner_lock_busy_count
foundation_runner_failed_count
foundation_runner_ok_count
```

For v1 manual validation, include profile from audit event fields:

```text
profile=v1-funded-read-only
```

## Alert Severity

P0:

- access token request fails with IP allowlist error on the GCP VM
- Secret Manager loading fails for `TOSS_CLIENT_ID` or `TOSS_CLIENT_SECRET`

P1:

- `foundation_snapshot_failed`
- `foundation_audit_failed` for `v0-empty-safe`
- latest source health is `blocked` or `error`

P2:

- `foundation_audit_failed` for `v1-funded-read-only` after a known manual test order
- missing order detail raw response
- missing execution delta after a filled order

## Non-Goals

Do not build these yet:

- Grafana dashboards
- Metabase analytics
- live order monitoring
- strategy P&L dashboards
- external data quality dashboards

Those come after Foundation v1 passes and paper order planner is introduced.

## Manual Health Check

On the VM:

```bash
tail -n 20 runtime/foundation_runner.jsonl
sqlite3 runtime/foundation_account_state.sqlite \
  "select source, channel, source_status, action, ts from source_health_snapshot order by ts desc limit 5;"
```

Healthy v0 state:

```text
foundation_runner_ok
foundation_snapshot_ok
foundation_audit_ok
source_status=ok
```
