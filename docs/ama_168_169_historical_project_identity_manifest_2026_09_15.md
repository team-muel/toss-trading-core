# AMA-168/169 historical GCP project identity manifest — 2026-09-15

Status: **PARTIALLY RESOLVED — retirement remains BLOCKED.** This is a
read-only identity investigation. It does not authorize a resource change,
service connection, API enablement, retirement action, or a conclusion that a
legacy deployment never existed.

## Question and evidence boundary

The historical repository default is `toss-trading-core-lab`. The question is
whether that exact value identifies a separate GCP project resource, is the
display name of the observable project, or is an unverified deployment
default.

The authenticated owner account was used for read-only Cloud Resource Manager,
Cloud Billing metadata, Cloud Audit Logs, and repository-history queries. The
account's project listing is an authority boundary, not a global inventory of
all projects to which it has no access.

## Authoritative project metadata

Cloud Resource Manager returned one accessible project whose **display name**
is exactly `toss-trading-core-lab`:

| Field | Observed value |
| --- | --- |
| Exact project resource ID | `toss-trading-core-lab-508411` |
| Display name | `toss-trading-core-lab` |
| Project number | `347216292745` |
| Lifecycle | `ACTIVE` |
| Creation time | `2026-09-12T11:15:12.412Z` |
| Billing metadata resource | `projects/toss-trading-core-lab-508411/billingInfo` |

The Cloud Audit Log `google.cloudresourcemanager.v3.Projects.CreateProject`
event records the same creation operation: request `name` is
`toss-trading-core-lab`, while `projectId`, project resource name, and project
number identify `toss-trading-core-lab-508411` / `347216292745`. The subsequent
billing-assignment audit event names the same numeric project resource. This
is authoritative evidence that the historical token is the display name of the
observable project; it is **not** that project's project ID.

A direct read-only `gcloud projects describe toss-trading-core-lab` did not
resolve an accessible project and returned the Cloud Resource Manager combined
not-found-or-not-authorized response. That response cannot distinguish an
inaccessible project from a nonexistent one, so it is not an absence proof.

## Historical deployment and build evidence

Repository history first introduced the old token as a default in
`964bb0039c1634de6d950a21bd6a7ac778429ffc` on 2026-07-21. It subsequently
appeared in Cloud Build, provisioning, systemd, and research-runtime defaults.
Those commits predate creation of the observable project by several weeks.
They therefore prove that a configuration default existed, but do not prove a
deployment in either the observable project or any other project.

Cloud Build list and trigger queries for `toss-trading-core-lab-508411` were
not run because `cloudbuild.googleapis.com` is disabled and this investigation
did not authorize enabling it. Cloud Build history, old deployment records, and
the historical research/build service-account binding therefore remain
`UNKNOWN` rather than being inferred from source defaults.

The existing read-only manifest for the observable project found no current
research runner/build service account, Compute resource, Scheduler job, bucket,
BigQuery dataset, or monitoring object. That result is scoped to
`toss-trading-core-lab-508411`; it is not evidence about any inaccessible
project.

## Determination and fail-closed disposition

| Candidate explanation | Evidence status | Disposition |
| --- | --- | --- |
| The token is the display name of `toss-trading-core-lab-508411` | Cloud Resource Manager, create-project audit event, and billing metadata agree | **CONFIRMED** |
| The token is the project ID of the observable project | Authoritative metadata gives a different project ID | **REFUTED** |
| A separate project with project ID `toss-trading-core-lab` never existed | Current principal cannot resolve that ID, but the error is ambiguous and the project list is access-scoped | **UNKNOWN** |
| Historical defaults describe an actual legacy deployment | Repository history contains defaults only; Cloud Build/deployment evidence is unavailable | **UNKNOWN** |

No same-project discovery manifest needs recollection: the exact current project
ID was already used in the approved read-only manifest. A separate project ID
has not been established, so no guessed-project discovery is permitted.

Retirement execution remains prohibited. A future investigation may query a
separately identified project only after its exact project ID, owner authority,
and read-only access are established. Enabling Cloud Build or other APIs,
connecting to a VM, disabling timers, changing Scheduler, or deleting IAM/data
is outside this manifest.
