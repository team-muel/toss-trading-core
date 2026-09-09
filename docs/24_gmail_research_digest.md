# Gmail research digest — retired side-channel

> **Operational status: retired.** The former Gmail research digest and its standalone scheduled research service are not part of the canonical Point-in-Time Asset Management OS. Do not enable email delivery, install the old research service, or start `toss-research-automation@daily.service` from repository history.

Historical digest messages, interpretation outputs, and immutable report artifacts may be retained as read-only evidence when their provenance is preserved. They do not confer recommendation, portfolio, risk, paper-broker, or execution authority.

## Current boundary

Research interpretation belongs upstream of the governed `asset_management` decision pipeline. Any information that is allowed to influence a production decision must enter through canonical PIT/lineage, Signal/Forecast, policy, and acceptance gates. A Gmail message is never a production control surface or an alternative decision channel.

The old `RESEARCH_EMAIL_*` activation configuration, Gmail OAuth delivery path, standalone timers, and manual service-start procedure are no longer supported. They must not be reintroduced as an independently scheduled cloud application.

## Retirement

Use `asset_management.cli.legacy_retirement` to inventory known legacy systemd units and narrowly scoped Foundation monitoring resources. The planner is dry-run by default; apply requires a reviewed fresh plan hash. Unknown unit names, changed unit identities, shared cloud resources, or unverified scope fail closed or remain for manual review.

Credentials, OAuth secrets, IAM, buckets, datasets, account data, and canonical OS resources are not automatically deleted by the retirement tool. Their handling remains part of explicit operational acceptance and secret-management procedures.

## Historical implementation references

Source history may still contain the previous interpretation, email-digest, reporting, and runner implementations for audit or reconstruction. Those historical paths are evidence only and are not supported runtime entry points in the current package.
