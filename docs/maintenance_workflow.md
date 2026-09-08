# Maintenance change workflow

Scope: AMA-149. Baseline: master@5d6e28b3e5ba64a552deea2119797f9b8b562329.

[Linear Maintenance Surface Register](https://linear.app/muelsyse/document/maintenance-surface-register-toss-trading-core-fd92fa5c5bb2)
owns responsibility and change intent. `config/maintenance_surfaces.json` is its
version-controlled watch-path projection. Update both when a responsibility or
path changes. This tool is repository tooling, not a packaged runtime resource.

## Evergreen responsibilities, finite changes

AMA-135 through AMA-147 remain open across fixes and releases. Reference them as
maintenance umbrellas; never use closing keywords for these issues. A finite
change issue owns acceptance and completion. Reuse an existing functional issue
when it already tracks the same change; do not duplicate work. Link a cross-surface
change to every relevant umbrella, not to artificial umbrella blockers.

`Maintenance Umbrella` and `Maintenance Change` distinguish these in Linear.
Milestone percentages including evergreen records are not feature readiness.
An Urgent umbrella records potential impact, not proof of an active incident.
Unassigned standing ownership remains unresolved rather than inferred from a PR.

## Offline routing

Run from a checkout with Python 3.11 or 3.12; no credentials or network are needed:

```bash
python scripts/check_maintenance_registry.py
python scripts/check_maintenance_registry.py config/toss_openapi_contract.json
```

The no-path invocation validates only the registry structure. Its JSON mode is
`registry-validation-only`; this is not a classification of the current PR.
For an actual change, obtain the diff against the PR's real target branch. Fetch
that ref beforehand. For a stacked PR, use its stack parent, not always master.
Run the following in a shell with `BASE_REF` set to that existing local ref:

```bash
set -e
: "${BASE_REF:?Set BASE_REF to the fetched PR target ref}"
base="$(git merge-base "$BASE_REF" HEAD)"
paths_file="$(mktemp)"
trap 'rm -f "$paths_file"' EXIT
git diff --no-renames --name-only -z "$base" HEAD -- > "$paths_file"
python scripts/check_maintenance_registry.py --paths0-from "$paths_file"
```

Disabling rename detection preserves both old and new paths, including deletions.
The intermediate file and `set -e` avoid hiding a failed git command in a pipeline.
Empty, truncated or malformed path input is an error, not evidence of no impact.
Paths containing spaces or UTF-8 names are retained. Control characters, absolute
paths, parent traversal and backslashes are rejected. This intentionally requires
canonical repository-relative POSIX paths; exceptional names need explicit review.

Exit codes: 0 = registry valid / supplied paths mapped; 1 = unmapped paths;
2 = invalid registry, path input or unreadable file. For an unmapped path, extend
the mapping and its regression test in the same change and sync Linear.

Patterns use case-sensitive Python `fnmatchcase`: `*` can include `/`, `**` is
not a special glob engine, and brace expansion is not supported. Every pattern
must start with a literal path/name prefix, not `*`, `?` or `[`. This rejects
wildcard-only catch-alls such as `***` and `?*` in both surface and cross-surface
rules while preserving `src/**`, `requirements*.lock` and similar scoped patterns.
Rules are additive, never first-match-wins. The watch-path inventory is an initial routing
map, not proof that every repository file or semantic dependency is covered.
Reviewers must add impacts beyond the report. General `tests/**` and `docs/**`
patterns identify control/documentation surfaces, not the subsystem being tested.

The conservative cross-surface rules expand provider, economics, migration,
execution, package/CI and research-bridge changes. Required evidence for each
umbrella is recorded in the JSON. Do not add broad catch-all routing merely to
make an unknown path pass. Routing is not authority or acceptance evidence.
The provider expansion also covers research collectors/providers, data-source
configuration, FRED series and the credential example; these must not silently
skip the External + Data + Schema + Tests dependency review.

`tests/test_maintenance_registry_adversarial.py` pins representative contract
examples independently of the JSON mapping. It also tests wildcard bypasses,
CLI failure paths and a real offline git rename diff. Tests generated solely
from existing rules cannot detect an omitted rule or path.

## CI rollout and evidence

The existing `test` matrix runs registry validation and pytest regressions for
push, pull_request and merge_group (and the existing schedule). Required names,
action pins, permissions, wheel smoke and trading safety checks remain intact.
PR body completeness and changed-file classification are **not automatically
enforced** by this first rollout. Populate the template and run the classifier;
mandatory PR metadata enforcement needs separate review and existing-stack rollout.

Attach change issue, affected umbrellas, PR, base/head and merge SHA, timestamp,
test scope/commands/results, reviewer disposition, limitations and rollback.
A revised head invalidates affected prior evidence. The team's review state is
In Progress plus the In Review label; unfulfilled post-merge acceptance uses
Validation and does not become Done. A local fixture is not real-account proof.

After acceptance and merge, close only the finite change, then append evidence
to relevant umbrellas. Registry/docs work alone never grants live authority or
marks other surfaces verified. Do not paste secrets or unredacted broker data.
Rollback this tooling through a reviewed revert; it has no runtime, schema
migration, account or live-mode effects. No new scheduled automation is created.
