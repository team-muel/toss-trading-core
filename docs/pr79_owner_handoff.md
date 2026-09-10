# PR #79: bounded self-review and owner handoff

## Baseline and stop condition

The user requested one additional hardening pass after #81 incorporation, no
Codex review request because quota was exhausted, one self-adversarial review,
and fixes for that pass only. Final integration and operational acceptance stay
with Amandalmoon. This is self-review, not independent approval.

Inspected parent: `9093cd46d0cbf4ede78115d9e661f5819ca96e52`.
The complete reconstructed source matches its tree
`4705e58f1da049a02fcf4c62eb385b42085632b9`. GitHub DNS is unavailable in the
local container; an existing hash-verified source artifact and the already
accepted six-file change reconstructed this tree. No new transport workflow
was created. The changes below are a draft child of #79, not a master merge.

## One self-adversarial pass: reproduced and repaired

Baseline focused regressions: 46 passed. The additional review suite exposed
**11 failing negative cases and 2 passing controls**, grouped as follows.

### R1 - direct risk-free materialization bypassed the artifact contract

D2 checked `fred-risk-free-curve@1` and RAW quality, but the public curve builder
used by direct consumers did not. Two otherwise well-shaped unapproved raw
artifacts produced usable curves. The materializer now rejects them itself;
the existing D2 wrapper test still constructs a caller-supplied curve so both
entry paths remain covered. Normal canonical materialization remains usable.

### R2 - model lineage hashes did not prove canonical scope/time binding

A caller could construct a valid-hash ModelCalculationBinding around a graph
whose final node declared another model/scope, or predate the authorization.
Publication and D2 consumption checked mutually consistent copies rather than
rerunning the binding owner. Six negative cases reproduced these paths.

Both boundaries now reuse `bind_authorized_model_calculation` at the claimed
binding instant and compare the rebuilt binding. Current publication/evaluation
authorization checks remain in place. D2 also requires the exact gold
`model-lineage-evidence@1` / VALID contract; an unapproved schema was the seventh
negative case in this group. The immutable store already rejects RAW-quality
gold writes; that is a passing upstream control, not a newly fixed defect.

These checks bind declared model/scope/time to a graph. They do not independently
execute every formula or prove the source data, economic usefulness, or factor
estimation lineage. The separate factor-risk runtime check remains blocked.

### R3 - partial GCP filter recognition could delete a shared metric

The retirement planner recognized one literal `metric.type` substring even
when the same retained policy used an OR/dynamic selector. It also ignored a
ratio condition's `denominatorFilter`. Both counterexamples incorrectly
selected a Foundation metric for deletion.

The planner now recognizes only complete conjunctions of literal equalities,
and examines denominator filters as well. Any selector outside that narrow
grammar preserves metrics for manual review. The positive control still permits
an eligible scoped metric when remaining policies contain only unrelated
literal selectors. This may retain more resources; it never assumes a complex
query is unrelated merely because one substring is understood.

## Validation

`tests/test_pr79_handoff_review.py` preserves the counterexamples and controls.
All 13 pass after repair. The broader local Python 3.13 suite passes **288**
tests across D2, model binding/lineage, risk-free, factor risk, retirement,
historical account/CLI boundaries, Quant, Macro, Fundamental and research
receipts. Secret, governance/live-disabled and instrument checks also pass.

Local full-suite/build is not claimed: DuckDB, Google auth and build are absent.
The normal locked-dependency Python 3.11/3.12 CI and installed-wheel checks for
the final draft head are recorded on GitHub separately. No safety test, required
check, action pin, workflow permission or merge protection was weakened.

## Original parent review disposition

Seven original #79 findings are verified as already repaired in incorporated
#81 and may be resolved on parent baseline `9093cd46`:

| Original comment | Verified disposition |
| --- | --- |
| 3966611304 | Installed CLI defaults use packaged resources; cwd regression and wheel smoke exist. |
| 3966611314 | Finalized SQLite audit is read-only; DELETE/WAL bytes and write rejection tested. |
| 3966611322 | Historical default remains `runtime/foundation_account_state.sqlite`. |
| 3966611330 | Fresh snapshot collector is absent from historical compatibility/package. |
| 3966611337 | D2 builder recomputes typed evidence; caller result tokens/fake sources are rejected. |
| 3966611346 | Factor assessment and policy estimator versions must match before publication. |
| 3966611352 | D2 rematerializes risk-free values from the stored observations. |

Three original threads remain open deliberately:

| Original comment | Remaining owner acceptance |
| --- | --- |
| 3966611334 | Actual VM inventory, reviewed retirement and proof legacy units are inactive. |
| 3966611341 | Actual raw-return-to-factor-estimator lineage/replay; fail-closed rejection is not completion. |
| 3966611360 | Actual GCP alert/metric retirement inventory and retained monitoring ownership. |

## Amandalmoon continuation

1. Inspect this draft's exact diff and CI, then decide whether to incorporate it
   into #79. This pass does not merge the draft, #79 or master, and does not ask
   for Codex/individual/team review or turn on auto-merge.
2. Reconcile #77 and any source PR supersession against the actual final #79
   tree. Do not independently reapply an older evidence implementation over
   these stronger checks. Keep factor-risk acceptance blocked until estimator
   lineage is genuinely implemented and verified.
3. Complete AMA-156/167 host/cloud ownership, preserved historical evidence,
   scoped retirement, consumer parity and no-second-scheduler proof. Use a
   quiescent maintenance window; the planner is not an atomic cloud/OS
   transaction. IAM, secrets, account data and live deployment were not touched.
4. Complete each theme's remaining acceptance and AMA-166 real-data OOS and
   Signal/Forecast calibration independently of these mechanism tests. Normal
   parent review, exact-head CI and protected Merge Queue still apply.

AMA-156, AMA-161 and AMA-167 remain In Progress. Existing owners and broader
acceptance criteria are unchanged; evergreen AMA-135 through AMA-147 are
references only. AMA-162's earlier scoped completion is not reopened here.
