# AMA-168/169 read-only resource discovery manifest — 2026-09-15

Status: **OBSERVED_ABSENT in `toss-trading-core-lab-508411`; not global
retirement proof.** This manifest contains read-only query results only. No
resource was stopped, disabled, modified, deleted, or recreated.

## Authority and scope

| Field | Value |
| --- | --- |
| Repository baseline | `master@757d823273fa418b27e8187a47b021a47dc5a223` |
| Observable project resource ID | `toss-trading-core-lab-508411` |
| Project number | `347216292745` |
| Discovery account | authenticated project owner account |
| APIs enabled with owner approval | `compute.googleapis.com`, `cloudscheduler.googleapis.com` |
| Secret values accessed | none |
| VM login attempted | none; no instance was discovered |

The historical deployment default, `toss-trading-core-lab`, is confirmed as
the display name of this observable project, not its project ID. Whether an
inaccessible, separate project with that old value as its own project ID ever
existed remains `UNKNOWN`. See
`docs/ama_168_169_historical_project_identity_manifest_2026_09_15.md`. This
manifest does not prove the absence of resources in an inaccessible or
otherwise unidentified project.

## Resource-by-resource observations

| Resource class | Expected legacy identity | Read-only query | Observed result | Retirement / rollback disposition |
| --- | --- | --- | --- | --- |
| Compute VM | `personal-research-agent-vm` in `us-central1-a` | `gcloud compute instances list` | No instances | No VM action; no systemd rollback target in this project. |
| Persistent disk | VM boot or attached disks | `gcloud compute disks list` | No disks | No disk action; retain no inferred backup claim. |
| Static IP | `toss-research-static-ip` | `gcloud compute addresses list` | No addresses | No address action. |
| systemd services | automation, prune, stock-recommendations | conditional SSH only if an instance exists | Not queried: no VM exists | No stop/disable action; unit files remain repository legacy inventory. |
| systemd timers | daily, weekly, prune, stock-recommendations | conditional SSH only if an instance exists | Not queried: no VM exists | No stop/disable action; no active state may be inferred. |
| Cloud Scheduler | none declared by repository | `gcloud scheduler jobs list` across all 30 listed locations | No jobs returned | No scheduler action. |
| Research runner/build service accounts | `toss-research-runner`, `toss-research-build` | `gcloud iam service-accounts list` | Neither exists; only the default Compute Engine account was returned | No IAM action. |
| Research GCS bucket | approved bucket was intentionally unspecified | `gcloud storage buckets list` | No buckets | No data or bucket action. |
| BigQuery reporting | `toss_research_reporting` | `bq ls` | No datasets | No data action. |
| Ops Agent | VM-local Google Cloud Ops Agent | conditional SSH only if an instance exists | Not queried: no VM exists | No agent action. |
| Logging metrics | research metrics | `gcloud logging metrics list` | No metrics | No monitoring action. |
| Monitoring dashboard/policies | `Toss Trading - Operations, Data Quality, Strategy` and research policies | `gcloud monitoring dashboards list`; `gcloud monitoring policies list` | No dashboards or policies | No monitoring action. |
| GCE / Scheduler audit trail | GCE instance and Cloud Scheduler resources | `gcloud logging read` for `gce_instance` and `cloud_scheduler_job` | No entries returned | Absence is scoped to this project and query access. |

## Queries and evidence handling

The discovery used the approved project ID explicitly for Compute, Scheduler,
IAM, Storage, BigQuery, Logging, and Monitoring list/read commands. Scheduler
locations were enumerated first, then queried individually; no job response was
returned. API enablement is the only GCP state change made for discovery.

Do not treat a list response as a substitute for account-truth, canonical
production evidence, Gate D2 acceptance, or live-trading authority.

## Retirement decision

No retirement execution target is currently established in the observable
project. The separate execution stage is therefore **BLOCKED**, not a no-op
retirement approval, because the historical deployment project identity remains
unresolved. Keep the repository legacy research runtime, seven systemd unit
definitions, and rollback instructions intact.

Before an execution PR can be reviewed, the owner must either:

1. establish that any separately identified historical project never hosted the
   legacy runtime, with authoritative project/resource evidence; or
2. provide access to its exact project resource ID and repeat this manifest
   there.

Only then may a separately reviewed execution PR name concrete retirement
targets, preserve their release/environment/log metadata, and sequence timer
disablement before service, IAM, data, or resource cleanup. AMA-156 remains In
Progress until that finite operational acceptance is complete.
