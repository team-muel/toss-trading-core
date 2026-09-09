"""Regressions for Codex third-round schema, group provenance and memory findings."""
from dataclasses import replace
from datetime import timedelta

import pytest

from alpha_management.campaign import run_expression_research
from asset_management.data.repositories import SQLiteTemporalObservationStore
from asset_management.domain.errors import DataQualityError
from test_research_campaign import IDS, repository_sessions, spec, append_group_evidence


def grouped_spec():
    return replace(spec(), expression="group_rank(ts_return(total_return_index,2),sector)", group_fields=("sector",))


def group_sessions(sessions, label="industry"):
    return [replace(session, resolver=replace(session.resolver, groups={"sector": {
        period: dict.fromkeys(IDS, label) for period in session.resolver.reference_periods
    }})) for session in sessions]


def test_same_manifest_does_not_approve_wrong_observation_schema(tmp_path):
    research_spec = spec()
    source, sessions = repository_sessions(tmp_path, research_spec)
    context = sessions[-1].context
    old = source.observations.series(entity_id=IDS[0], field="total_return_index", context=context,
        dataset_manifest_id=sessions[-1].dataset_manifest_ids[0])[-1]
    known = old.available_at + timedelta(seconds=1)
    SQLiteTemporalObservationStore(source.observations._conn).append(
        observation_id="wrong-schema-vintage", entity_id=old.entity_id, field=old.field,
        value="99999", reference_period=old.reference_period, event_time=old.event_time,
        scheduled_release_at=None, official_release_at=None, source_timestamp=known,
        received_at=known, available_at=known, ingested_at=known, revised_at=known,
        supersedes_observation_id=old.observation_id, source_timezone="UTC",
        schema_version="unapproved-raw-price", dataset_manifest_id=old.dataset_manifest_id)
    with pytest.raises(DataQualityError, match="SCHEMA"):
        run_expression_research(research_spec, sessions)


def test_complete_caller_group_labels_are_not_pit_evidence(tmp_path):
    research_spec = grouped_spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    with pytest.raises(DataQualityError, match="MISSING"):
        run_expression_research(research_spec, group_sessions(sessions))


def test_run_receipt_is_incremental_not_full_prefixes(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    run = run_expression_research(research_spec, sessions)
    assert run.payload()["schema_version"] == "expression-research-run-v2"
    assert "session_input_deltas" in run.payload()
    assert "session_inputs" not in run.payload()


def test_canonical_group_evidence_cannot_be_replaced_by_different_caller_labels(tmp_path):
    research_spec = grouped_spec()
    source, sessions = repository_sessions(tmp_path, research_spec)
    append_group_evidence(source, sessions)
    with pytest.raises(DataQualityError, match="PROVENANCE_MISMATCH"):
        run_expression_research(research_spec, group_sessions(sessions, label="fabricated"))


def test_future_group_observation_is_not_available_for_history(tmp_path):
    research_spec = grouped_spec()
    source, sessions = repository_sessions(tmp_path, research_spec)
    append_group_evidence(source, sessions, future=True)
    with pytest.raises(DataQualityError, match="MISSING"):
        run_expression_research(research_spec, group_sessions(sessions))


def test_canonical_group_evidence_identity_is_in_the_receipt(tmp_path):
    research_spec = grouped_spec()
    source, sessions = repository_sessions(tmp_path, research_spec)
    append_group_evidence(source, sessions)
    run = run_expression_research(research_spec, group_sessions(sessions))
    last = list(run.iter_session_inputs())[-1]
    assert last["group_evidence"]["sector"][0][IDS[0]]["observation_id"].startswith("group-0-")
    assert last["observation_evidence_hashes"]["total_return_index"][IDS[0]]


def test_evaluated_cross_sections_reuse_the_same_history_kernel(tmp_path):
    from alpha_management.history import simulate_history, _last_cross_section
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    scores = [_last_cross_section(research_spec.compiled, session, session.instrument_ids) for session in sessions]
    assert simulate_history(research_spec.compiled, sessions, research_spec.settings, evaluated_scores=scores) == simulate_history(
        research_spec.compiled, sessions, research_spec.settings)
    for invalid in (scores[:-1], [dict.fromkeys(IDS, float("nan")) for _ in sessions],
                    [{"unknown": 1} for _ in sessions]):
        with pytest.raises(ValueError):
            simulate_history(research_spec.compiled, sessions, research_spec.settings, evaluated_scores=invalid)


def test_incremental_journal_size_scales_with_new_cells_and_replays_revisions():
    from alpha_management.input_journal import InputJournal, iter_snapshots
    from copy import deepcopy
    import json
    def build(count):
        journal = InputJournal()
        latest = None
        for index in range(count):
            latest = {"axis": list(range(index + 1)), "fields": {str(i): list(range(index + 1)) for i in range(40)}}
            if index >= count - 2:
                latest["fields"]["0"][0] = -99
            journal.append(latest)
        deltas = journal.finish()
        assert list(iter_snapshots(deltas))[-1] == latest
        return len(json.dumps(deltas))
    short, long = build(40), build(80)
    assert long < 2.5 * short


def test_journal_budget_fails_explicitly_instead_of_unbounded_retention(monkeypatch):
    from alpha_management import input_journal
    monkeypatch.setattr(input_journal, "MAX_JOURNAL_BYTES", 10)
    with pytest.raises(ValueError, match="budget"):
        input_journal.InputJournal().append({"fields": [1] * 100})


def test_mutated_delta_cannot_replay_as_original_input(tmp_path):
    import json
    from alpha_management.campaign import ResearchRun
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    run = run_expression_research(research_spec, sessions)
    payload = run.payload()
    payload["session_input_deltas"][0].append(["set", ["instrument_ids"], ["unknown"]])
    forged = ResearchRun(run.result, json.dumps(payload))
    with pytest.raises(ValueError, match="hash mismatch"):
        list(forged.iter_session_inputs())
