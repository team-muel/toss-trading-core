# GCP research-data automation — retired

> **Operational status: retired.** This document no longer authorizes or describes a supported standalone research deployment. Do not provision, install, enable, or start the former `toss-research-*`, `toss-stock-recommendations*`, `toss-foundation*`, or `toss-paper-operation*` units from repository history.

The repository now has one operational identity: the Point-in-Time Asset Management OS through the canonical `asset_management` runtime. `research_platform` and `alpha_management` are upstream research capabilities only; they are not separately scheduled cloud applications and do not own account, risk, portfolio, paper-broker, or execution authority.

## Historical evidence

Previously collected immutable datasets, hypotheses, QA reports, and replay artifacts may remain readable as historical evidence when they satisfy the canonical PIT and lineage contracts. Retaining those artifacts does **not** retain authority to run the old collectors or schedulers.

## Retirement procedure

Legacy systemd resources are inventoried with the fail-closed retirement planner:

```bash
python -m asset_management.cli.legacy_retirement systemd
```

The command is dry-run by default. Any apply operation requires an explicitly reviewed, fresh `plan_sha256` and revalidates the recorded unit identities before the first destructive command. Unknown unit names or changed unit files fail closed.

GCP monitoring resources are handled separately with an explicit project and instance scope:

```bash
python -m asset_management.cli.legacy_retirement gcp --project <project-id> --instance-id <numeric-instance-id>
```

This is also dry-run by default. Shared or dynamically scoped resources remain for manual review. IAM, secrets, buckets, datasets, account data, and canonical OS resources are outside the automatic retirement surface.

## Current rule

Do not reactivate the historical daily/weekly/prune services or their installer/provisioner scripts. New research consumers must enter the canonical immutable/PIT data and governed Signal/Forecast path owned by `asset_management`; production adoption is accepted only through the corresponding OS gates and current runbooks.
