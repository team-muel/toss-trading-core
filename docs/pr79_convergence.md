# PR #79 convergence: one runtime, internal research capabilities

## Decision and source coordinates

On 2026-09-09 the user instructed this research reconstruction to converge at
PR #79 and incorporate Seo Jeongseo's latest AMA-156 single-runtime direction,
without abandoning ongoing research development. This is a child integration
of PR #79, not a force-push or overwrite of its author's branch.

Baseline: PR #79 `a2a7f2b5e0b6ce817c97f56e9f8a5a26a8fc24a1`.
Research sources: PR #78 `79d893f01e0b0eb82538996e9fe24506f3e0c00c`, its
previously unpublished third-review correction, and PR #80
`111c900eb83dead75a41699a737f332668cf69ac`. Previous Macro/Fundamental checkpoint
patches were reconstructed against this actual baseline and retested, not
accepted merely because earlier logs reported success.

## Delivery and ownership

`asset_management` is the only production application. `alpha_management`
provides numerical research. `research_platform` preserves internal/offline
research algorithms and historical data/validation utilities, not another
scheduled cloud application or independent account/risk/paper authority.

AMA-156/PR #79 owns the runtime and deployment boundary. AMA-162/163 own the
research receipt and Quant models; AMA-164/165 own Macro/Fundamental adapters.
AMA-166 still owns real-data OOS diagnostics and existing Signal/Forecast
integration. AMA-167 still owns actual consumer cutover and operational
retirement evidence. No issue is done merely because a child PR passes CI.

## Implemented model mechanisms

* Research receipt v2 verifies observation schema, PIT classification evidence,
  both ordered instrument axes, immutable manifests and consumed values. Sparse
  input deltas avoid retaining every expanding numeric prefix. A 256 MiB encoded
  journal limit rejects rather than truncates. Full-prefix CPU work and caller
  session structures are not claimed to be optimized.
* All six Quant templates compile to the same canonical AST/evaluator; a
  template is not a separate backtester. Golden tests compare legacy rebalance
  scores where economic meaning is unchanged. Ties, undefined ratios and missing
  active composite components have documented fail-closed semantics.
* ALFRED uses the existing raw-first immutable adapter. Complete vintages,
  inclusive end dates, revision tombstones, conservative date-only publication
  lag and contiguous monthly history are checked. The model emits `MACRO_STATE`,
  not cross-sectional ranks or a portfolio allocation.
* Fundamental inputs reuse the retained driver/GAAP/cash-flow validator. Actual
  source/policy/proposal artifacts, periods, currency and EPS basis are checked;
  seven named numeric research features are returned without recommendation,
  portfolio weights, stock-return forecasts or execution authority. Source text
  interpretation is explicitly unverified; these are not approved FeatureStore
  outputs. The recovered checkpoint used incompatible function/field names;
  this integration repaired them and reran its tests.

These tests use synthetic fixtures. They do not establish empirical alpha,
source licensing approval, actual market-data readiness or paper/live acceptance.

## Codex PR #79 corrections

1. Runtime validator defaults resolve from the corresponding source tree or
   installed package resources, never an arbitrary process working directory.
   CI invokes the installed console command outside the checkout.
2. Historical audit uses a truly read-only SQLite reader without constructing
   the old writer or changing journal mode. The original deployed database
   default `runtime/foundation_account_state.sqlite` is preserved.
3. The historical reader accepts finalized, quiescent archives, not active
   databases. Nonempty WAL/rollback journals are rejected, never checkpointed or
   deleted. `immutable=1` is suitable only when the operator guarantees the file
   will not change. Tests assert identical bytes/journal state and no new files.
4. The fresh broker snapshot collector is no longer shipped in compatibility.
   Its old behavior survives only in test fixtures used to construct synthetic
   historical evidence, not in the installed wheel.
5. Gate D2 builds runtime checks from actual typed source evidence, not a
   caller-made mutable result object. FRED rates are rematerialized from the
   stored observations. Missing/forged bodies or altered rates fail closed.
6. Factor-risk assessment/policy estimator versions must match. More importantly,
   the currently unimplemented raw-return-to-estimator provenance cannot be
   certified by a stored copy or arbitrary Tiingo parent. Both old and new factor
   receipts are denied runtime acceptance until that real derivation is built.
   New v2 records are explicitly `MECHANISM_ONLY`; this is a known blocked
   capability, not a claim to have implemented the estimator.
7. Old application entry points fail closed. Removing a service definition alone
   does not stop an installed VM unit or delete a cloud alert. The explicit
   retirement command below inventories and applies a narrowly scoped plan.

The original PR #79 review threads remain unresolved until the author actually
incorporates and verifies these child changes. A reply pointing here is not a
claim that the parent head is fixed.

## Retired standalone execution

The old Foundation/Paper runtime remains removed. Research/stock service and
timer definitions are removed from supported deployment. Their old installers,
provisioners, VM runners, identity checks and prune entry points now exit 78
before any operation. Standalone automation, LLM hypothesis planning, recommendation,
report delivery and GCS uploader CLI entry points refuse execution, including
`python -m` invocation. Offline pure algorithms and historical evidence readers
remain available; numerical research is not deleted.

The old test that required provisioning a second research application was
replaced with negative activation tests. Historical data/backtest/replay,
formatting, licensing, API and algorithm regressions remain in the suite.
No test changes make an invalid authority grant acceptable.

## Explicit retirement procedure (not executed by this change)

Use a maintenance window with no concurrent unit/resource changes. Archive
finalized account/research evidence first. Do not pass credentials in arguments
or commit generated inventory containing sensitive operational details.

Plan the existing VM units on that VM:

```sh
python -m asset_management.cli.legacy_retirement systemd
```

The command only lists units, inspects fragment paths and hashes exact allowed
unit files. Unknown legacy names or vendor unit paths require manual review.
Review the plan and repeat with its SHA to apply:

```sh
python -m asset_management.cli.legacy_retirement systemd --apply --expect-plan-sha256 REVIEWED_SHA
```

Use the required local administrative identity; the command does not obtain or
escalate permissions. It disables/stops known timers before services, removes
only the exact reviewed unit files, reloads systemd and checks inactive state.
No broker/account DB, environment file or canonical runtime unit is removed.

Plan GCP alert retirement with explicit identifiers (no project/instance default):

```sh
python -m asset_management.cli.legacy_retirement gcp --project PROJECT_ID --instance-id NUMERIC_INSTANCE_ID
```

Only historical Foundation policies with known names and exact instance/metric
filters are selected. Their fully qualified IDs and current inventory hash are
in the plan. Policies are deleted before eligible metrics. Metrics referenced
by other policies, dynamic selectors or unverified scopes are retained for
manual review. Review the plan before applying with `--apply --expect-plan-sha256`.
The CLI rereads inventory on each invocation; a changed plan requires new review.

These commands were tested with mocks and synthetic files only. No actual VM
services or GCP resources were changed in this work. Reconcile remaining
research-only alerts, Ops Agent pipelines, IAM/service identities, secrets,
bucket/BigQuery/dashboard ownership and canonical monitoring separately in
AMA-156/167. They are not silently deleted or claimed migrated. The retained
Cloud Build identity builds this one package; its real IAM/operational adoption
also requires inventory. `scoped_retirement_applied=true` is not Gate G approval.

## Verification and integration rule

Dependency-complete local tests, wheel installation outside checkout, runtime
READ_ONLY validation, governance, secret scan, actual-path maintenance routing
and shell checks are recorded on the child PR and Linear against exact hashes.
Remote CI and independent Codex review must be evaluated on the final head.
Do not merge unresolved findings, skip protected master checks, or treat the
child's passing tests as production/account/economic acceptance.

Retargeting after PR #79 merges requires reconciliation with its actual master
SHA and renewed tests. Source PR #78/#80 can be closed as superseded only after
this integration is accepted; do not accidentally apply their pre-cleanup
namespace/deployment assumptions over the new runtime.
