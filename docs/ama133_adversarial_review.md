# AMA-133 adversarial review: Codex quota fallback

## Authority and scope

On 2026-09-08 the user explicitly authorized adversarial self-review and merge
after the required checks, because Codex rejected the exact-head re-review due
to quota. This is not independent Codex approval. Branch protection, full CI,
merge-group checks, and the prohibition on live authority remain unchanged.

The reviewed baseline was `87171d6941dae4941bd60b533173a51c5346127f`.
The original Codex findings, revised authority/PIT code, neighboring tests,
repository contracts, and schemas were inspected. An isolated audit branch
exported the exact tracked source for local testing; its helper workflows are
not part of the integration PR.

## Findings and disposition

| Area | Severity | Reproduction and repair |
| --- | --- | --- |
| Approval-shaped object | P1 | A SimpleNamespace bypassed the approval constructor. OrderIntent now requires the concrete governor-issued approval type. |
| Cash identity | P1 | Relabeling SPY as residual cash reversed a REDUCE decision. Policy and token now bind an explicit cash instrument; policy-authorized custom IDs remain supported. |
| Mutable validated inputs | P1 | Frozen policy retained a mutable multiplier dictionary. Freeze the copied policy, hash its full content, reject approvals after policy replacement, and detach intent target lists and forecast matrices. |
| Recovery watermark and race | P1 | A new CANCEL during lookup retained UNKNOWN and was overwritten by old OPEN evidence. Compare audit generation inside the commit transaction, reject backdated operations, retain the maximum historical marker, and require post-operation request/observation/completion evidence. |
| Forecast validity | P1/P2 | LINEAR forecasts remained unchanged at the midpoint; STEP/EXPONENTIAL remained nonzero at expiry. Apply and record one forecast-validity discount, preserve costs and uncertainty conservatively, and make expiry exclusive. |
| Correlation/PSD | P2 | An impossible correlation matrix passed. Require PSD for both matrices. Rounded sqrt checks also rejected 25 of 100 deterministic valid singular Gram matrices; exact rational Schur complements preserve them. |

All repairs have executable cases in `tests/test_ama133_adversarial_review.py`.
Existing submission, governor, horizon, and integrated replay/paper tests remain
in place. Submission fixtures gained the observation columns present in the
production schema; no risk gate was relaxed to accommodate fixtures.

## Evidence

Before production edits, the first negative-test batch produced **11 failures
and 1 passing control** on the pinned baseline. This is direct reproduction,
not an inference from unresolved review status.

The local Python 3.13 environment lacked google-auth and DuckDB. Its broad
supplemental run passed **1251 tests**, with 7 dependency-related skips,
3 dependency-related deselections, and the Google-upload test module excluded.
No committed pytest configuration or test was disabled. This local result is
not the required Python 3.11/3.12 acceptance evidence. Full installed-dependency
CI results and the exact final/merge-group SHAs must be recorded in PR #62.

## Limits

The review covers executable software invariants, not investment profitability,
real broker account truth, or live operational approval. Private Python object
internals are not a security sandbox. Credentials and actual accounts were not
used. Missing Toss accounting/NAV/settlement evidence remains fail-closed.
