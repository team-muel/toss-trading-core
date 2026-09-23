# AMA-182 — canonical ApplicationRuntime deployment preflight

## Purpose and authority

This is a plan-only deployment manifest for one future, canonical
`ApplicationRuntime.boot → PipelineEvidenceRepository → DecisionKernel`
workload. The manifest and its validator are offline review artifacts. They do
not enable APIs, create identities or resources, deploy code, create a
scheduler, change secrets, or grant execution authority.

The checked-in manifest is deliberately `NOT_READY`. Its project identity is
copied from `config/research_operations_identity.json`; the 2026-09-15
resource-discovery record is scoped historical evidence, not a current cloud
observation. In particular, the empty approved bucket registry is binding:
no artifact or data destination is approved. No bucket, service account,
registry, image digest, backup policy, IAM grant, or resource is invented here.

## One proposed workload model

Use one operator-invoked Compute Engine VM for the canonical application, with
one attached zonal Persistent Disk for the SQLite evidence database. Run the
application as a single systemd service with no inbound network listener and
no timer, Cloud Scheduler job, or other automated invocation. Do not co-host
legacy research, stock-recommendation, paper-broker, or Foundation units. Any
future activation mode beyond `READ_ONLY` requires its own explicit authority
and acceptance; this manifest does not grant it.

The VM and data disk do not exist as an approved/current deployment target.
Project, zone, machine shape, disk identity, image URI/digest, bucket, and
backup destination therefore remain unresolved. Compute Engine Persistent
Disk is a durable block-device option for the proposed SQLite file, but this
plan does not claim that a disk or backup exists. See the official
[Persistent Disk overview](https://docs.cloud.google.com/compute/docs/disks/persistent-disks).

Runtime and deployer service-account *names* are proposed separately in the
manifest; their existence and grants are `NOT_OBSERVED`. The runtime identity
must be user-managed, attached only to this workload, and receive resource-
scoped minimum grants after the actual artifact, secrets, and logging targets
are approved. The deployer identity must be separate and must not be available
to the runtime. For a VM, use the `cloud-platform` OAuth scope and constrain
effective access with IAM grants on the narrowest applicable resources, as
described by Google's [service-account guidance](https://docs.cloud.google.com/compute/docs/access/service-accounts).
No role binding is requested or applied by this change.

## API and data boundaries

The manifest lists a proposed API allowlist only. All entries are marked
`NOT_ENABLED_BY_THIS_CHANGE`. APIs outside the list are not implied. Cloud
Scheduler, Cloud Build and broker-write APIs are excluded from this workload
plan. No API is enabled by running the local preflight command.

The intended SQLite file path is on the future data disk. Local VM storage is
not an evidence backup. A concrete backup destination, retention, restore test,
and rollback owner are required before deployment; until then the preflight
blocks. `config/research_operations_bucket_registry.json` has no approved
buckets and must remain fail-closed. Secret names/versions are also unresolved;
the manifest stores no values and never reads secrets.

## Preflight and failure behavior

Run `python scripts/check_application_runtime_preflight.py`. It validates the
manifest schema, pins the project ID/number to the repository-owned identity,
rejects the prohibited display-name-as-project-ID, checks runtime/deployer
separation, enforces read-only/no-ingress/no-scheduler/no-deploy declarations,
compares bucket identities to the approved registry, and reports unresolved
deployment evidence. It performs local file reads only.

The expected result today is `BLOCKED` with missing approved artifact registry
and immutable image digest, no approved data/backup bucket, unobserved service
accounts and target resources, and no backup/restore evidence. A missing,
stale, conflicting, or unverifiable value remains blocked; a caller-supplied
non-empty resource name is not approval. The validator has no mode that creates
or updates cloud resources.

## Future operator remediation and post-deployment evidence

After this plan is reviewed, a separately authorized operator may prepare a
concrete change that supplies observed project/zone/resource IDs, approved
artifact digest and bucket registry entry, named service accounts and scoped
grants, secret-version references, backup/restore plan, and rollback image/data
snapshot. A different review and explicit deployment approval are required
before any API enablement or resource mutation. This document is not that
approval.

If a future separately approved deployment occurs, its evidence record must
bind source SHA, image digest, manifest hash, exact project and resource IDs,
IAM policy export, API state, runtime mode, migration versions, evidence-store
identity, backup/restore result, logging/monitoring configuration, scheduler
inventory, observation timestamps and the actor/approval. It must distinguish
current-project observations from unobservable historical environments.

Rollback is a reviewed revert to the previous immutable image plus a verified
pre-change data snapshot. Never restore or activate a legacy second runtime as
an automatic rollback. No retirement command or physical deletion is part of
this preflight.
