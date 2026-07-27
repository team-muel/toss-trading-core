# Cloud Monitoring Runner Health

## Purpose

This document defines the minimum health checks for the GCP foundation runner.

The original monitoring scope was operational health only. On 2026-07-27 the
operator approved a private GCP dashboard for operations, research-data quality,
and verified strategy performance. Grafana and Metabase remain out of scope;
the approved extension uses Cloud Monitoring, BigQuery, and private GCS.

## Health Sources

The runner produces four local evidence sources:

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
- no `foundation_runner_ok` event for eight hours while the six-hour timer is enabled
- latest event is `foundation_runner_failed`
- latest event is `foundation_snapshot_failed`
- latest event is `foundation_audit_failed`
- expected `foundation_runner_backup_ok` event is missing after a successful audit
- `FOUNDATION_GCS_BACKUP_URI` is set but `foundation_runner_backup_upload_ok` is missing
- repeated `foundation_runner_lock_busy` events indicate cron or systemd timer overlap
- latest `source_health_snapshot.source_status` is not `ok`
- Toss error action is `register_current_ip_in_toss_openapi_allowlist`
- v1 audit fails after a manual funded validation run

## Deployed Monitoring State — 2026-07-21

The production-lab project currently has:

- Ops Agent collection of the foundation runner JSONL log
- all ten suggested log-based event counters
- alert policies for runner failure, snapshot failure, audit failure, runner
  heartbeat absence, backup-upload heartbeat absence, and runner lock contention
- a verified operator email notification channel attached to the alert policies

The systemd timer runs the read-only v0 foundation job every six hours. A
successful manual run after the Toss key rotation emitted successful broker,
audit, local backup, and Cloud Storage upload evidence.

The checked-in Ops Agent configuration is:

```text
deploy/ops-agent/toss-foundation.yaml
```

Install it without changing Secret Manager or runner data:

```bash
sudo install -m 0644 deploy/ops-agent/toss-foundation.yaml \
  /etc/google-cloud-ops-agent/config.yaml
sudo systemctl restart google-cloud-ops-agent
sudo systemctl is-active google-cloud-ops-agent
```

Verify that the JSONL receiver is producing structured Cloud Logging entries:

```bash
gcloud logging read \
  'resource.type=gce_instance AND jsonPayload.event=foundation_runner_ok' \
  --project=toss-trading-core-lab \
  --freshness=24h \
  --limit=5
```

All six policy templates and ten log-metric definitions are stored under
`deploy/monitoring/`. Policy templates contain `__INSTANCE_ID__` and
`__NOTIFICATION_CHANNEL__` placeholders so a rendered policy is bound to the
one Foundation VM and the verified operator channel. Never submit an
unrendered template.

Render the six templates into an isolated directory before comparing them with
the deployed policies:

```bash
python scripts/render_foundation_monitoring.py \
  --output-dir /tmp/toss-foundation-monitoring \
  --instance-id "<numeric-gce-instance-id>" \
  --notification-channel \
  "projects/toss-trading-core-lab/notificationChannels/<channel-id>"
```

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
- missing or empty `FOUNDATION_TARGET_ORDER_ID` for a v1 run
- missing execution delta after a filled order

## Non-Goals

The former dashboard restriction is superseded by
`docs/22_visual_reporting.md`. The remaining non-goals are:

- Grafana dashboards
- Metabase analytics
- live order monitoring
- public or unauthenticated reporting
- unverified strategy P&L
- automatic promotion of research results into live orders

All operational failure and heartbeat-absence policies must have a verified
notification channel. A policy without a channel creates a console incident but
does not notify the operator.

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
