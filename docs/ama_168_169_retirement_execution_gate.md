# AMA-168/169 retirement execution gate

Parent readiness evidence: merged PR #104, `AMA-168/169 retirement readiness`,
at `master@af0d575c14b70812e57a03aa808bce3f99ada7cc`.

Status: **BLOCKED — no concrete retirement target is authorized.**

## Observed scope

The approved read-only discovery of
`toss-trading-core-lab-508411` found no Compute VM, disk, static IP, Cloud
Scheduler job, research service account, bucket, BigQuery dataset, logging
metric, monitoring dashboard, or monitoring policy. No VM means no systemd or
Ops Agent instance was available to inspect.

The historical repository default is confirmed as the display name of the
observable project, not that project's ID. A separate inaccessible project
with the same historical value as its project ID is still `UNKNOWN`; source
defaults are not deployment evidence. Therefore this execution stage must not
infer that the legacy runtime is globally absent or retired.

## Historical-project deletion disposition

The project owner reports that the historical project has been deleted. That
fact is recorded as **owner-provided disposition**, not elevated to an
independently retrievable deletion record: this read-only account cannot read a
Cloud Resource Manager record for `toss-trading-core-lab`, and the API's direct
lookup response deliberately combines not-found with not-authorized. `gcloud
projects list` documents that deleted and pending-deletion projects are omitted;
the accessible listing contains only the current project whose display name is
the historical token.

The following evidence was re-checked without enabling an API, contacting a
VM, or changing a resource:

| Evidence source | Observed boundary | Acceptance value |
| --- | --- | --- |
| Cloud Resource Manager | No retrievable historical project metadata, number, deletion timestamp, or deletion actor in this access scope | Historical-resource lifecycle proof is **UNRECOVERABLE**. |
| Billing | The current billing account lists current active bindings only; it supplies no historical binding for the deleted identifier | Historical billing/retention proof is **UNRECOVERABLE**. |
| Audit Logs | The observable project's log scope has no historical-project `DeleteProject` event; a deleted project's own log scope is unavailable | Historical deletion, VM, Scheduler, systemd, and deployment-event proof is **UNRECOVERABLE**. |
| Repository history | Old project defaults occur in deployment, Cloud Build, and systemd sources before the observable project's creation | Shows intended configuration only; it is not runtime or retirement proof. |
| Observable-project discovery | Read-only lists found no Compute VM/disk/address, Scheduler job, research service account, bucket, dataset, metric, dashboard, or policy in `toss-trading-core-lab-508411` | Supports a scoped **OBSERVED_ABSENT** finding for the current project only. |
| Remaining build/log/artifact evidence | Cloud Build is disabled and no bucket/dataset was observed; no immutable historical artifact location is identified | Historical artifact retention proof is **UNRECOVERABLE**. |

`UNRECOVERABLE` means evidence cannot be recreated from a deleted project with
the available authority. It does not mean the operational acceptance passed,
the resource never existed, or deletion was correctly performed.

## Current authoritative acceptance

The current Linear descriptions for AMA-168 and AMA-169 authoritatively
supersede the former requirement to prove a normal operational VM or GCP
retirement. The former environment is classified as a failed historical
environment that was abandoned and is no longer authoritative; this record does
not claim that a formal retirement procedure occurred.

The active acceptance requires all of the following:

1. Identify the current canonical Google Cloud account/project and its governed
   operational runtime.
2. Verify that current repository, CI, deployment, scheduler, and authority
   configuration has no executable reference or authority path to the historical
   environment.
3. Verify that the current authorized canonical environment has no second
   scheduled trading/research application. This is not a claim about an
   inaccessible historical environment's physical state.
4. Record `OBSERVED_ABSENT` only for the exact current-project scope.
5. Record unavailable historical lifecycle, systemd, command, rollback, billing,
   audit, monitoring, and scheduler evidence as `UNRECOVERABLE` without treating
   it as retirement completion, correct deletion, global absence, or acceptance
   completion.
6. Preserve the approved-bucket-identity fail-closed guard: every provisioning,
   runtime, and Cloud Build artifact destination must resolve against a
   repository-owned approved bucket identity. A non-empty caller value is
   insufficient.
7. Authorize no retirement command unless a newly identified concrete target,
   reviewed manifest, explicit approver, and rollback evidence exist.
8. Do not reactivate, contact, or delete the historical environment under this
   acceptance.

This document does not claim that the active acceptance is satisfied. Current
evidence identifies the approved project and records a scoped read-only
observation, but does not establish the canonical environment's governed runtime
or deployed-state evidence. AMA-168/169 acceptance therefore remains
**BLOCKED**. AMA-156 remains In Progress; Gate D2, canonical run, M5, and live
remain BLOCKED. This PR remains Draft and must not enter Merge Queue.

## Execution preconditions

Before this Draft PR may contain a retirement command, a reviewed manifest must
identify a concrete resource in every field below:

| Required field | Reason |
| --- | --- |
| Exact project resource ID and owner | Prevent retirement in a guessed or display-name-only project. |
| Resource type, name/ID, region/zone, observed status | Define the target precisely. |
| VM release target, unit hashes, timer/service state, and non-secret environment metadata | Establish a systemd rollback path. |
| Data retention owner and evidence location | Prevent accidental removal of research or audit evidence. |
| Explicit retirement approver and rollback-window end | Separate authorization from discovery. |

## Approved future execution shape

Only a later reviewed revision may add commands, and it must proceed in this
order:

1. Preserve the rollback artifacts listed above.
2. Disable only named timers first; do not delete unit files or releases.
3. Wait for named one-shot services to finish or receive an explicit incident
   disposition.
4. Re-read timer/service/monitoring state and attach the result to the
   manifest.
5. Treat IAM revocation, secret removal, data deletion, address/disk deletion,
   and source-code cleanup as separately approved changes.

This gate does not change GCP, VM, systemd, Scheduler, IAM, data, secrets, or
deployment state. It does not alter AMA-156's In Progress status, Gate D2,
canonical production runs, M5, or live-trading authorization.
