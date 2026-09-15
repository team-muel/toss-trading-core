# AMA-168/169 legacy research operation retirement readiness

Status: **NOT READY — no operational resource was changed.**

This is a reviewed, read-only readiness record for the legacy research runtime.
It is not authorization to stop, disable, delete, or recreate any GCP or VM
resource. AMA-156 remains In Progress. Gate D2, canonical production runs, M5,
and live trading remain blocked.

## Scope and observation boundary

Repository baseline: `master@757d823273fa418b27e8187a47b021a47dc5a223`
(AMA-156 Phase 2). Read-only observations were collected on 2026-09-15 from the
authenticated project owner account.

The observable project is:

| Field | Observed value |
| --- | --- |
| Project resource ID | `toss-trading-core-lab-508411` |
| Project number | `347216292745` |
| Display name | `toss-trading-core-lab` |
| Lifecycle | `ACTIVE` |
| Creation time | `2026-09-12T11:15:12.412Z` |
| Current operator authority | authenticated account has `roles/owner` |

The prior deployment default used `toss-trading-core-lab` as a project
identifier. Authoritative Cloud Resource Manager metadata and the project
creation audit event establish that it is the display name of
`toss-trading-core-lab-508411`, not that project's resource ID. They do not
establish whether an inaccessible, separate project with the old value as its
project ID ever existed. The detailed, fail-closed result is recorded in
`docs/ama_168_169_historical_project_identity_manifest_2026_09_15.md`.

This remains an identity mismatch, not evidence that it is safe to retire
anything. The repository has a single checked identity declaration in
`config/research_operations_identity.json`: every GCP shell entry point
validates the actual project ID before its first GCP call, and CI rejects the
prior identifier in an operational target. Artifact destinations are separately
governed by the repository-owned
`config/research_operations_bucket_registry.json`; it currently contains no
approved bucket, so provisioning, runtime upload, and Cloud Build artifact
upload all fail closed regardless of caller-supplied bucket or URI values.

## Current evidence

| Surface | Repository-declared identity / path | Read-only observation | Retirement conclusion |
| --- | --- | --- | --- |
| Compute VM | `personal-research-agent-vm`, `us-central1-a`, `toss-research-static-ip`, `toss-research-runner` | Compute Engine API was enabled with owner approval; no instance, disk, or address was returned, so SSH/systemd state could not be read. | `UNKNOWN`; do not retire. |
| Scheduler | Four VM systemd timers; no Cloud Scheduler definition is in the repository | Cloud Scheduler API was enabled with owner approval; no job was returned from the 30 listed locations. | `UNKNOWN`; do not retire. |
| VM services | `toss-research-automation@.service`, `toss-research-prune.service`, `toss-stock-recommendations.service` | Their unit files are versioned, but active/failed state and running jobs need a read-only VM query. | `UNKNOWN`; do not retire. |
| VM timers | daily, weekly, prune, and stock-recommendations timers | The repository intends `enable --now`; actual enablement cannot be read without a discovered VM. | `UNKNOWN`; do not retire. |
| Monitoring | Ops Agent config, logging metrics, BigQuery reporting, dashboard name `Toss Trading - Operations, Data Quality, Strategy` | No dashboard or research logging metric was returned; only default log sinks were observable. This absence does not prove deletion. | `UNKNOWN`; do not retire. |
| Storage / reporting | explicitly approved `RESEARCH_GCS_URI`, `toss_research_reporting` | No research bucket, dataset, or service-account identity was returned to the current read-only list commands. | `UNKNOWN`; do not retire. |

The project has no observable GCE-instance, Cloud Scheduler, Compute audit, or
Cloud Scheduler audit entries under the current read-only query. This remains a
scoped, insufficient absence proof rather than a retirement result.

## Deployment and rollback surface

`scripts/install_research_automation_vm.sh` installs all seven unit files and
enables the four timers. The units execute from
`/home/seoje/toss-trading/current`, write under `research-runtime` or
`stock-recommendation-runtime`, and source `/etc/toss-trading/research.env`.
The provisioning script declares a Compute VM, static address, runner/build
service accounts, research bucket, logging metrics, BigQuery reporting, and a
monitoring dashboard. The active release target, environment file, VM disk and
any real resource IDs have not been captured.

Rollback must therefore remain possible before retirement: preserve the current
release target, unit-file hashes, environment-file metadata (never values),
timer/service state, recent journal/log cursor, attached service account, and
resource IDs. Do not delete releases, buckets, service accounts, secrets,
datasets, dashboards, addresses, disks, or audit logs as part of a first
retirement action.

## Reviewed retirement plan — not executed

1. **Reconcile authority and project identity.** The owner records the exact
   project resource ID, intended retirement owner, and whether the legacy
   deployment ever targeted the inaccessible `toss-trading-core-lab` identifier.
   No guessed project or resource may be substituted.
2. **Obtain explicit approval for any additional discovery API.** Compute Engine
   and Cloud Scheduler approval was recorded for this discovery; any additional
   API enablement still changes project state and requires separate user
   approval. The minimum API and role set is listed below; it is a request list,
   not an instruction to grant roles or enable APIs.
3. **Run the approved read-only discovery.** Enumerate instances, disks,
   addresses, service accounts, buckets, datasets, logging metrics, dashboards,
   and Cloud Scheduler jobs in the reconciled project. For each discovered VM,
   obtain only: `systemctl is-enabled`, `systemctl is-active`, `systemctl
   list-timers`, `systemctl cat`, release symlink target, and non-secret
   environment-file metadata. Record resource IDs, owner, status, deployment
   SHA, and evidence timestamps.
4. **Review a per-resource retirement manifest.** The manifest must identify
   every one of the seven unit files, all enabled timers, dependent running
   service instances, monitoring/reporting resources, ownership, data-retention
   requirement, rollback artifact, and a named approver. Missing fields block
   retirement.
5. **Only after approval, execute a separately reviewed retirement change.**
   First disable timers without deleting their units; wait for any active
   one-shot jobs to finish or be explicitly dispositioned; verify no new runs
   occur across the relevant schedules. Keep units, release target, data, and
   credentials intact for the agreed rollback window. Resource deletion, IAM
   revocation, and data-retention cleanup are distinct, later changes.
6. **Validate and close only finite work.** Re-read timer/service state and
   monitoring evidence, attach the immutable retirement manifest, and confirm
   rollback instructions. AMA-156 may be reassessed only after AMA-168/169
   operational retirement acceptance is complete.

## Approval-gated read-only discovery request

The following is the smallest intended discovery set. Existing enabled APIs may
be queried only after approval; `compute.googleapis.com` and
`cloudscheduler.googleapis.com` must not be enabled by an agent.

| Surface | API | Minimum discovery role |
| --- | --- | --- |
| Project / enabled services | Service Usage | `roles/serviceusage.serviceUsageViewer` |
| VM, disks, addresses | Compute Engine | `roles/compute.viewer` |
| VM systemd state | Compute Engine + IAP only if the VM requires it | `roles/compute.osLogin` and `roles/iap.tunnelResourceAccessor`, in addition to viewer; no sudo or write command |
| Scheduler jobs | Cloud Scheduler | `roles/cloudscheduler.viewer` |
| Service accounts | IAM | `roles/iam.serviceAccountViewer` |
| Buckets | Cloud Storage | `roles/storage.viewer` |
| BigQuery metadata | BigQuery | `roles/bigquery.metadataViewer` |
| Logs / metrics / dashboards | Logging + Monitoring | `roles/logging.viewer`, `roles/monitoring.viewer` |

After explicit approval, set the following value exactly and run only these
read-only commands. They form the source material for the resource-by-resource
manifest; none enables an API, changes a resource, reads a secret value, or
starts/stops a service.

```bash
PROJECT_ID=toss-trading-core-lab-508411
ZONE=us-central1-a
INSTANCE_NAME=personal-research-agent-vm

gcloud services list --enabled --project="$PROJECT_ID"
gcloud compute instances list --project="$PROJECT_ID"
gcloud compute disks list --project="$PROJECT_ID"
gcloud compute addresses list --project="$PROJECT_ID"
gcloud scheduler jobs list --project="$PROJECT_ID" --location=-
gcloud iam service-accounts list --project="$PROJECT_ID"
gcloud storage buckets list --project="$PROJECT_ID"
bq --project_id="$PROJECT_ID" ls
gcloud logging metrics list --project="$PROJECT_ID"
gcloud monitoring dashboards list --project="$PROJECT_ID"
gcloud logging read 'resource.type="gce_instance"' --project="$PROJECT_ID" --limit=100

gcloud compute ssh "$INSTANCE_NAME" --project="$PROJECT_ID" --zone="$ZONE" \
  --tunnel-through-iap --command='systemctl is-enabled toss-research-daily.timer toss-research-weekly.timer toss-research-prune.timer toss-stock-recommendations.timer; systemctl is-active toss-research-automation@daily.service toss-research-automation@weekly.service toss-research-prune.service toss-stock-recommendations.service; systemctl list-timers --all; systemctl cat toss-research-automation@.service toss-research-prune.service toss-stock-recommendations.service; readlink -f /home/seoje/toss-trading/current; stat -c "%n %a %U:%G %s %y" /etc/toss-trading/research.env'
```

The manifest must record: project resource ID, resource type/name/ID, region or
zone, observed status, owner, deployment SHA or release target, source command,
timestamp, retention classification, rollback artifact, and a non-secret hash
or metadata reference. Any inaccessible item remains `UNKNOWN` and blocks
retirement.

## Explicit prohibitions

- Only owner-approved Compute Engine and Cloud Scheduler API enablement occurred
  before discovery; no VM was contacted and no systemd/GCP resource was changed
  by this record.
- Do not infer a production authority, accounting truth source, canonical run,
  Gate D2 pass, M5 eligibility, or live-trading permission from this work.
- Do not use fixture output, a missing API response, or documentation alone as
  proof that a legacy resource is absent or retired.
