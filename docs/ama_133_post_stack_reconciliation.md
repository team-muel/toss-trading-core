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

Local CI-equivalent verification completed on the reconciliation branch:

- all 1,359 tests passed using the repository development lockfile;
- secret, maintenance-registry, governance, OpenAPI 1.2.15, and research
  instrument validation passed;
- shell syntax and ShellCheck passed for every CI-targeted script;
- the wheel built successfully and, from a clean external environment, loaded
  the packaged Gate D2, risk-free, and liquidity-risk resources and booted the
  runtime migration set.

GitHub still must run the protected 3.11/3.12 test matrix and merge-queue
checks on the final PR SHA. Gate D2/E evidence is therefore review input, not
an authorization to merge or enable live trading.
