# Agent operating contract

This repository is a high-risk trading research and execution foundation. Agents must preserve explicit authority boundaries and fail-closed behavior.

## Source of truth

- Linear owns scope, priority, dependencies, and delivery state.
- GitHub owns implementation evidence: commits, pull requests, reviews, CI, releases, and deployments.
- Notion may hold durable architecture and runbooks, but repository contracts and tests govern executable behavior.

When a Linear issue key is present in a branch, PR, or prompt, use it to understand intent and acceptance criteria. Do not invent requirements that conflict with repository contracts.

## Non-negotiable invariants

1. Never enable live trading implicitly.
2. Never bypass or weaken risk, authorization, reconciliation, replay, or governance gates to make a test pass.
3. Preserve fail-closed behavior for stale, conflicting, incomplete, or unverifiable evidence.
4. Keep research, paper, shadow, and live semantics explicit. Do not silently promote artifacts across modes.
5. Preserve point-in-time semantics and lineage. Future information must not leak into historical or prospective calculations.
6. Treat broker/account state and immutable evidence as authoritative where documented.
7. Do not commit credentials, tokens, private account data, or unredacted broker responses.

## Before implementation

- Read the relevant docs, schemas, tests, and neighboring implementation before editing.
- Identify the authority boundary and expected failure behavior.
- Prefer the smallest coherent change that satisfies the requested contract.
- Preserve deterministic replay and idempotency where the affected subsystem supports them.

## Before requesting review

Run the checks applicable to the change. For broad changes, mirror the repository CI baseline:

```bash
python -m pytest -q
python scripts/check_toss_openapi.py
python -m research_platform.cli.research_validate_instruments
python -m build --wheel
python -c "import asset_management.orchestration.runtime"
```

When packaging behavior changes, also verify the built wheel installs and can load its packaged resources outside the checkout, matching CI. For shell changes, run `bash -n` and `shellcheck` on the affected scripts.

Document:

- what changed and why,
- failure modes,
- regression surface,
- verification performed,
- any known limitation or deferred work.

## Review protocol

Review in this order:

1. Correctness and semantic invariants.
2. Authority and fail-closed boundaries.
3. Point-in-time data integrity and lineage.
4. Idempotency, replay, and recovery behavior.
5. Security and secret handling.
6. Negative-path and regression tests.
7. Maintainability.

Material review findings must be fixed or explicitly dispositioned. Do not merge with unresolved correctness, security, data-integrity, or governance findings.

## Merge protocol

`master` is protected by a repository ruleset (2026-09-07): pull requests only, `test (3.11)` and `test (3.12)` are required status checks, review threads must be resolved, and merges go through the GitHub Merge Queue. Queue a PR with `gh pr merge <number> --merge` (or the "Merge when ready" button); the queue rebuilds it on top of `master`, runs CI on the `merge_group` event, and merges only when the required checks pass. Do not push to `master` directly. Emergency hotfixes follow the organization review-completion rule (Linear MUE-63): use the admin bypass only for a documented incident, and open the follow-up review immediately.

## Maintenance change routing

Follow `docs/maintenance_workflow.md` and the PR template. Run
`python scripts/check_maintenance_registry.py` for registry integrity and classify
actual changed paths against the PR target before review. The no-path invocation
is not a PR classification. Add semantic impacts beyond path-based suggestions.
Reference AMA-135 through AMA-147 as evergreen umbrellas, never with closing
keywords. Only finite changes are completed after their scoped acceptance.
Keep Linear responsibility/evidence and the repository mapping synchronized.
This routing never authorizes live trading or replaces the review protocol.
