# Visual reporting — historical/retired deployment surface

> **Operational status: retired for standalone research deployment.** Historical report artifacts and dashboards may be inspected as evidence, but this document must not be used to provision, refresh, or start the former standalone research application.

The canonical operational runtime is `asset_management`. Reporting that contributes to a governed decision must consume canonical PIT/lineage evidence and must not create a parallel scheduler, portfolio/risk authority, recommendation service, or execution path.

## Retained historical evidence

Existing immutable report JSON/HTML objects, historical BigQuery rows, dashboard snapshots, and log records may remain readable when their provenance is preserved. Their existence does not prove current runtime adoption and does not authorize the former `toss-research-automation@daily.service` or related timers.

## Retirement boundary

Do **not** run the former `scripts/provision_research_automation_gcp.sh`, `scripts/install_research_automation_vm.sh`, or any `systemctl start toss-research-*` command from repository history.

Legacy systemd and narrowly scoped Foundation monitoring resources must first be inventoried with `asset_management.cli.legacy_retirement`. The planner is dry-run by default, requires a reviewed plan hash for apply, rejects unknown/stale unit identities before any destructive action, and preserves shared or unverified cloud resources for manual review.

IAM, secrets, buckets, datasets, account data, Ops Agent configuration, and canonical OS monitoring remain outside automatic deletion. Their final disposition belongs to the Point-in-Time Asset Management OS operational acceptance process.

## Current reporting rule

Any new dashboard, metric, or durable report must be attributable to the canonical Asset Management OS or to read-only historical evidence. Standalone research reporting infrastructure must not be recreated as an independently scheduled application.
