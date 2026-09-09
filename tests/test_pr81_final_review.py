"""Codex regressions and a separately recorded self-adversarial review.

All fixtures are synthetic. No systemctl/gcloud command reaches a real host.
"""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from alpha_management.campaign import ResearchRun, run_expression_research
from alpha_management.metrics import SimulationResult
from asset_management.cli.legacy_retirement import apply_plan, identity, systemd_plan
from asset_management.data.alfred import SCHEMA_VERSION
from asset_management.domain.errors import DataQualityError
from asset_management.features.macro_state import MacroStateSpec, run_macro_state
from test_canonical_macro_state import inputs, context, RECEIVED, LICENSE
from test_research_campaign import repository_sessions, spec


# Codex: performance metrics are not part of a mechanism-only receipt.
def test_mechanism_receipt_rejects_injected_performance_metrics(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    run = run_expression_research(research_spec, sessions)
    forged = replace(run.result, metrics=SimulationResult(99.0, 9.0, 0.0, 99.0, 0.0, 252))
    with pytest.raises(ValueError, match="mechanism-only.*metrics"):
        ResearchRun(forged, run.evidence_json)


# Codex: well-shaped silver rows cannot launder an unapproved raw contract.
@pytest.mark.parametrize("schema,quality", [
    ("unapproved-schema", "RAW"), ("approved", "VALID"),
])
def test_macro_rejects_unapproved_bronze_parent(tmp_path, schema, quality):
    store, manifests = inputs(tmp_path)
    silver, rows = store.read(manifests["DGS2"])
    bronze, raw = store.read(silver.parent_manifest_ids[0])
    metadata = dict(source=bronze.source, dataset=bronze.dataset,
                    retrieved_at=RECEIVED, available_at=RECEIVED,
                    provider_timestamp=RECEIVED, license_tag=LICENSE,
                    code_revision="synthetic-review", request_hash="b" * 64)
    forged_raw = store.write(raw, layer="bronze", schema_version=bronze.schema_version if schema == "approved" else schema,
                             quality_status=quality, **metadata)
    forged_silver = store.write(rows, layer="silver", schema_version=silver.schema_version,
                               parent_manifest_ids=(forged_raw.manifest_id,), **metadata)
    manifests["DGS2"] = forged_silver.manifest_id
    macro_spec = MacroStateSpec()
    with pytest.raises(DataQualityError, match="MACRO_RAW_LINEAGE_INVALID"):
        run_macro_state(macro_spec, store=store, manifests=manifests, context=context(macro_spec))


TEMPLATE = "toss-research-automation@.service"
INSTANCES = ("toss-research-automation@daily.service", "toss-research-automation@weekly.service")


def template_runner(root, *, template=None):
    fragment = str(root / TEMPLATE) if template is None else str(template)
    def run(argv):
        if argv[1] == "list-unit-files":
            return f"{TEMPLATE} disabled"
        if argv[1] == "list-units":
            return "\n".join(f"{name} loaded active running" for name in INSTANCES)
        if "--property=FragmentPath" in argv:
            return fragment
        if "--property=ActiveState" in argv:
            return "inactive"
        return ""
    return Mock(side_effect=run)


# Codex: daily/weekly instances use the template's fragment, not separate files.
def test_retirement_accepts_and_binds_instantiated_template(tmp_path):
    template = tmp_path / TEMPLATE
    template.write_text("[Service]\nExecStart=/usr/bin/true\n")
    runner = template_runner(tmp_path)
    plan = systemd_plan(run=runner, root=tmp_path, host="synthetic-host")
    assert TEMPLATE in plan["unit_files"]
    assert set(plan["units"]) == set(INSTANCES)
    assert not any(command[-1] == TEMPLATE for command in plan["commands"])
    apply_plan(plan, identity(plan), run=runner)
    assert not template.exists()
    assert all(["systemctl", "disable", "--now", name] in
               [call.args[0] for call in runner.call_args_list] for name in INSTANCES)


def test_template_changed_after_planning_has_zero_effects(tmp_path):
    template = tmp_path / TEMPLATE
    template.write_text("old")
    runner = template_runner(tmp_path)
    plan = systemd_plan(run=runner, root=tmp_path)
    template.write_text("changed")
    runner.reset_mock()
    with pytest.raises(ValueError, match="unit changed before retirement"):
        apply_plan(plan, identity(plan), run=runner)
    runner.assert_not_called()
    assert template.read_text() == "changed"


def test_vendor_template_outside_reviewed_root_is_rejected(tmp_path):
    (tmp_path / TEMPLATE).write_text("local")
    runner = template_runner(tmp_path, template="/usr/lib/systemd/system/" + TEMPLATE)
    with pytest.raises(ValueError, match="outside reviewed systemd root"):
        systemd_plan(run=runner, root=tmp_path)
    assert all(call.args[0][1] in {"list-unit-files", "list-units", "show"}
               for call in runner.call_args_list)


# Self-adversarial pass: mutate reconstructed output after admission.
def test_self_review_freezes_reconstructed_point_and_manifest_sequences(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    run = run_expression_research(research_spec, sessions)
    manifests = list(run.result.points[-1].dataset_manifest_ids)
    points = [*run.result.points[:-1], replace(run.result.points[-1], dataset_manifest_ids=manifests)]
    rebuilt = ResearchRun(replace(run.result, points=points), run.evidence_json)
    before = rebuilt.payload()["result"]
    points.pop()
    manifests.append("unrelated-source")
    assert len(rebuilt.result.points) == len(before["points"])
    assert list(rebuilt.result.points[-1].dataset_manifest_ids) == before["points"][-1]["dataset_manifest_ids"]
    assert rebuilt.evidence_hash == run.evidence_hash


# Self-adversarial pass: a result hash is not permission to change its meaning.
@pytest.mark.parametrize("mutation", ["scope", "spec", "boolean-result"])
def test_self_review_rejects_receipt_semantic_substitution(tmp_path, mutation):
    import json
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    run = run_expression_research(research_spec, sessions)
    payload = run.payload()
    if mutation == "scope":
        payload["validation_scope"] = "PRODUCTION_APPROVED"
    elif mutation == "spec":
        payload["spec"]["thesis"] = "Unrelated hypothesis under the same receipt"
    else:
        last = payload["result"]["points"][-1]["raw"]
        for instrument, value in last.items():
            if value == 1.0:
                last[instrument] = True
                break
        else:
            raise AssertionError("fixture must include a 1.0 raw score")
    with pytest.raises(ValueError):
        ResearchRun(run.result, json.dumps(payload))


def changing_unit_runner(root):
    name = "toss-foundation.service"
    state = {"fragment": str(root / name), "dropins": ""}
    def run(argv):
        if argv[1] in {"list-units", "list-unit-files"}:
            return f"{name} loaded active running"
        if "--property=FragmentPath" in argv:
            return state["fragment"]
        if "--property=DropInPaths" in argv:
            return state["dropins"]
        if "--property=ActiveState" in argv:
            return "inactive"
        return ""
    return state, Mock(side_effect=run)


# Self-adversarial pass: fragment hashes exclude effective drop-in overrides.
def test_self_review_dropins_require_manual_review(tmp_path):
    (tmp_path / "toss-foundation.service").write_text("original")
    state, runner = changing_unit_runner(tmp_path)
    state["dropins"] = str(tmp_path / "toss-foundation.service.d/override.conf")
    with pytest.raises(ValueError, match="drop-in.*manual review"):
        systemd_plan(run=runner, root=tmp_path)
    assert all(call.args[0][1] in {"list-units", "list-unit-files", "show"}
               for call in runner.call_args_list)


@pytest.mark.parametrize("change", ["dropin", "fragment"])
def test_self_review_rebind_after_plan_has_no_destructive_effects(tmp_path, change):
    unit = tmp_path / "toss-foundation.service"
    unit.write_text("original")
    state, runner = changing_unit_runner(tmp_path)
    plan = systemd_plan(run=runner, root=tmp_path)
    if change == "dropin":
        state["dropins"] = str(tmp_path / "toss-foundation.service.d/override.conf")
    else:
        state["fragment"] = str(tmp_path / "canonical-os.service")
    runner.reset_mock()
    with pytest.raises(ValueError, match="unit runtime identity changed before retirement"):
        apply_plan(plan, identity(plan), run=runner)
    assert unit.read_text() == "original"
    assert all(call.args[0][1] == "show" for call in runner.call_args_list)


def test_self_review_unbound_transient_unit_requires_manual_review(tmp_path):
    state, runner = changing_unit_runner(tmp_path)
    state["fragment"] = ""
    with pytest.raises(ValueError, match="no reviewed file identity"):
        systemd_plan(run=runner, root=tmp_path)
    assert all(call.args[0][1] in {"list-units", "list-unit-files", "show"}
               for call in runner.call_args_list)
