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

## Proposed superseding acceptance — pending Linear approval

AMA-168/169 may be **superseded or waived only for the unrecoverable physical
retirement proof**, and only after Linear explicitly accepts all of the
following as the replacement acceptance record:

1. Attach the owner-provided deletion disposition and any independently retained
   Cloud Resource Manager, billing, or audit export available outside this
   account's read-only scope. Missing export remains `UNRECOVERABLE`; it must
   not be replaced with a claim of absence.
2. Attach the dated current-project discovery manifest, including the exact
   project ID and the resource-by-resource `OBSERVED_ABSENT` results.
3. Retain the repository deployment/default inventory as historical context,
   explicitly labelled non-operational evidence.
4. Preserve the merged approved-bucket fail-closed guard: every provisioning,
   runtime, and Cloud Build artifact destination must remain resolved against a
   repository-owned approved bucket identity. A non-empty caller value is
   insufficient.
5. Preserve the execution gate with no retirement command because no concrete
   target, rollback artifact, or historical before/after evidence exists.
6. Keep AMA-156 In Progress and keep Gate D2, canonical production runs, M5,
   and live trading blocked. A waiver for historical operations does not grant
   any of those authorities.

Until a Linear acceptance change or independently approved superseding evidence
is attached, this proposal is **BLOCKED**. It does not mark AMA-168, AMA-169,
or AMA-156 Done and it does not authorize a Merge Queue action.

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
