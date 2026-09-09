# AMA-133 post-stack reconciliation

This reconciliation starts from current `master` after AMA-133 integration.  The
old #63–#74 pull-request stack is evidence only; it must not be merged as a
stack because it is based on `7b9468951feef0ae398be03e3244f4d81b197085`.

## Disposition

| Old PR | Source revision | Current-master disposition |
| --- | --- | --- |
| #63 | `098da1f` | Transplanted: contextual liquidity-risk assessment. |
| #64 | `2e1aa06` | Not transplanted: the OpenAPI 1.2.15 approval is already present through AMA-133. |
| #65 | `8588371` | Transplanted: contextual stress and event-risk controls. |
| #66 | `0a5b707` | Transplanted: Gate D2 pricing/expectation/risk acceptance. |
| #67 | `0c9f9ec` | Transplanted and reconciled with existing availability-time and manifest lineage. |
| #68 | `ed3a46f` | Transplanted: authorized model-relative-alpha assessment. |
| #69 | `3a4c1f7` | Transplanted: PIT factor and specific-risk assessment. |
| #70 | `69a114a` | Transplanted: model-scope calculation lineage binding. |
| #71 | `a1243cf` | Transplanted: planned capital-flow reserve assessment. |
| #72 | `696ff40` | Transplanted: duplicate confidence risk-scaling rejection. |
| #73 | `1e2f1f0` | Transplanted: optimizer return-semantic restriction. |
| #74 | `c82bb51` | Transplanted: typed economic trade evidence. |

## Authority and safety

- `master` remains the integration authority; no old branch is rebased or
  merged wholesale.
- Risk-free return reconciliation retains the newer `available_at` and
  immutable `dataset_manifest_id` checks, in addition to the old branch's
  currency, horizon, compounding, and formula alignment checks.
- The reconciliation does not grant operational authority or enable live
  trading.  Missing or stale pricing, account, risk, or PIT evidence remains
  fail-closed.

## Verification scope

Focused happy- and failure-path tests cover each transplanted contract.  The
full CI matrix, including Gate D2/E on the final reconciliation SHA, remains
required before review or merge.
