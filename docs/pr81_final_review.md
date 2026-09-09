# PR #81 final review: mechanism evidence and scoped retirement

Baseline: `9b4a4a685a8c00957aa2add70008ada6513adfea` (tree
`2dc7f657c76826ced54396ca8871fe6fb83e8ca5`). Target is PR #79, not master.
This checkpoint follows the user's 2026-09-10 instruction to address Codex,
perform one self-adversarial review, repair it, and close accepted finite work.
The self-review is not an independent review or investment approval.

## Codex findings and disposition

1. **Unbound performance metrics (P1):** `ResearchRun` is mechanism-only and now
   rejects every non-`None` metrics value. Performance evaluation is a separate
   OOS/calibration responsibility; no performance fields are silently omitted
   from the evidence while remaining observable in the result.
2. **Unapproved ALFRED raw contracts (P1):** macro rematerialization checks the
   bronze parent's exact source, dataset, layer, RAW quality and versioned raw
   schema identity. The approved identity is the actual adapter encoding
   `SCHEMA_VERSION:raw:sha256(canonical(RAW_SCHEMA))`, not the bare version name.
   The ingestion and consumer share `RAW_SCHEMA`; unchanged valid ingestion
   remains accepted. A well-shaped silver child cannot legitimize other raw
   contracts.
3. **Template instances rejected (P2):** a known daily/weekly instance may use
   its exact corresponding allowlisted template under the reviewed root. The
   actual template fragment is hashed even without a separate instance file.
   Other templates and vendor paths remain rejected. Templates are never sent
   to `systemctl disable --now` as abstract instances.

The first synthetic reproduction produced five failing cases and one passing
outside-root control. After correcting the real raw-schema encoding, the
Codex-focused and existing campaign/macro/retirement suites passed 146 tests.

## One self-adversarial pass

The review challenged reconstruction after admission, semantic substitutions,
and changes in effective systemd configuration. Eight new cases failed before
repair, grouped into three additional findings:

- **Mutable reconstructed result:** a frozen result could retain caller-owned
  point/manifest lists. Admission now copies these to tuples and rebuilds each
  immutable point; subsequent caller mutation cannot change the admitted run.
- **Receipt meaning was unchecked:** changing MECHANISM_ONLY to an approval
  scope, changing the hypothesis without its hash, or replacing a numeric JSON
  score with a boolean passed the old result check. Admission now verifies
  scope, spec hash/expression/settings/output semantics, result status and
  type-sensitive JSON result identity. Nonfinite/bool scores are rejected.
- **Effective unit configuration was not fully reviewed:** main-fragment
  hashes did not cover drop-ins, transient units without files, or manager
  fragment rebinding after planning. Drop-ins and unbound transient units now
  require manual review. Recorded fragment paths and absence of drop-ins are
  rechecked before the first destructive command, after file revalidation.

A maintenance window with quiescent configuration writers remains mandatory.
There is no atomic transaction spanning filesystem checks, the systemd manager,
and cloud APIs. A root administrator changing files after the final check is
not prevented by a hash. No actual host/cloud retirement was run in these tests.

## Reproduction and acceptance boundary

`tests/test_pr81_final_review.py` holds the reproducible counterexamples and
positive controls. Together with campaign, Quant, Macro, Fundamental and
retirement regressions, local Python 3.13 passed 228 tests after repair. Full
Python 3.11/3.12 CI, installed-wheel checks and the exact final Git head are
recorded on the PR, not inferred from this local subset.

Hashes bind content; they do not authenticate market data, prove a narrative,
or grant trading authority. Input-journal integrity is verified when replayed
through `iter_session_inputs`; this constructor is not an OOS validator.
AMA-166 real-data OOS, factor-risk estimator lineage, and AMA-156/167 actual
consumer/deployment retirement acceptance remain separate, open requirements.
An accepted child merge does not by itself finish those requirements or approve
master deployment. No live/paper promotion or broker write authority is added.
