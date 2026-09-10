# AMA-168 / AMA-169 operational retirement evidence — 2026-09-11

## Scope and guardrails

* GCP project: `toss-trading-core-lab`.
* VM: `personal-agent-vm` in `us-central1-a`, instance ID
  `3791592729602116851`. The inventory contained no other VM.
* This record concerns the retired Foundation scheduler and its dedicated
  observability only. It does not change IAM, Secret Manager, datasets,
  buckets, broker credentials, account data, or a canonical OS unit.

## VM retirement

The host initially exposed only these recognised legacy units:

* `toss-foundation.timer`: enabled and active (waiting).
* `toss-foundation.service`: disabled and inactive.

No drop-ins were present. The reviewed unit file SHA-256 values were:

* timer: `84d6b8ec50bc2fdd5a01d793bcb672bd34371bd1fc11a099972dd665bdb2f72e`
* service: `5db90f3ac98ad88cf1efc73ad8453e099b400d8a2634fff2916c85578524eee0`

The reviewed `legacy-retirement-plan-v1` plan hash was
`1b949a1696dbbd58c3ffb8f41766f4ae1a285ad850778a8debfda1e43a567263`.
It contained exactly these commands, in this order:

1. `systemctl disable --now toss-foundation.timer`
2. `systemctl disable --now toss-foundation.service`

The plan excluded account databases, artifacts, environment files, and
canonical units. It revalidated file and effective-fragment identities before
changing either unit. The post-apply inventory found no Foundation, Paper,
research-automation, or stock-recommendation systemd units; root and `seoje`
crontabs plus system cron locations had no matching scheduler entry.

## GCP observability retirement

The GCP planner initially retained all candidates because the historic alert
filters were not instance-ID-qualified. Manual scope review established that
the project has one VM, the legacy scheduler above was retired, and all six
remaining policies and nine metrics are Foundation-only event names with no
other policy consumer.

Policies selected for deletion:

* `10541506336125098357` — Foundation runner lock contention
* `12509684897906037025` — Foundation runner heartbeat missing
* `14811517552457197156` — Foundation runner failure
* `4625808170876309989` — Foundation snapshot failure
* `4625808170876312270` — Foundation audit failure
* `7834978347266213450` — Foundation backup upload heartbeat missing

Metrics selected for deletion:

* `foundation_audit_failed_count`
* `foundation_audit_ok_count`
* `foundation_runner_backup_ok_count`
* `foundation_runner_backup_upload_ok_count`
* `foundation_runner_failed_count`
* `foundation_runner_lock_busy_count`
* `foundation_runner_ok_count`
* `foundation_snapshot_failed_count`
* `foundation_snapshot_ok_count`

Each policy's filter selected only its corresponding
`logging.googleapis.com/user/foundation_*` metric on `gce_instance` resources.
The GCP inventory contained no non-Foundation policy and no second VM that
could emit those event names.

## Post-apply result

The VM plan was applied only with the reviewed hash above. The final systemd
and cron inventories returned no Foundation, Paper, research-automation, or
stock-recommendation scheduler. Both retired unit files are absent.

The six listed alert policies and nine listed Foundation custom metrics were
then removed from `toss-trading-core-lab`. A fresh project inventory reported:

* `REMAINING_FOUNDATION_POLICY_COUNT=0`
* `REMAINING_FOUNDATION_METRIC_COUNT=0`

The generic GCP planner's prior no-command plan is retained as conservative
evidence: it refused to delete unqualified historic filters automatically.
Manual review used the single-VM inventory and exclusive Foundation event names
to establish the narrower deletion scope above.

## Verification limitation

On the Windows operator workstation, the focused retirement/handoff suite
reported 25 passing tests and one skipped-equivalent environmental failure: the
symlink-target mutation test could not create a symlink because the account has
no Windows symlink privilege. This is not a planner assertion failure and does
not affect the Linux VM plan that was actually applied. The protected PR's
Linux CI must still pass for this revised evidence head.

## Recovery and review boundary

This is a retirement, not a promotion. If rollback is explicitly approved, a
reviewed deployment can restore the two version-controlled unit definitions and
recreate only the documented Foundation observability objects. It must not
restore a second runtime silently. Independent review must verify the final
post-apply inventories and the remaining factor-estimator lineage blocker
before AMA-156 / PR #79 can enter the Merge Queue.
