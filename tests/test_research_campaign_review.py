"""Regression evidence for the two Codex findings on PR #78."""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from alpha_management import HistoricalSession, RepositoryPanelResolver, simulate_history
from alpha_management.campaign import ResearchRun, run_expression_research
from asset_management.domain.errors import DataQualityError
from asset_management.time.asof import AsOfContext
from test_research_campaign import IDS, T, repository_sessions, settings, spec, append_group_evidence


def nonmember_sessions(tmp_path, research_spec):
    """Use a second real universe with one member, but read both known assets."""
    source, sessions = repository_sessions(tmp_path, research_spec)
    source.universes.include(
        membership_id="subset-membership", universe_id="synthetic-subset",
        instrument_id=IDS[0], inclusion_reason="synthetic subset fixture",
        effective_from=T - timedelta(days=2), available_at=T - timedelta(days=2),
        source="synthetic",
    )
    return source, [replace(session, instrument_ids=IDS[:1], resolver=replace(
        session.resolver,
        universe_membership={period: frozenset(IDS[:1]) for period in session.resolver.reference_periods},
    ), universe_version="unspecified") for session in sessions]


# Codex PR #78 R1: the resolver may include known nonmembers. Every snapshotted
# instrument, not just the current universe, must be recoverable from a receipt.
def test_replay_coordinates_preserve_extra_resolver_instruments(tmp_path):
    from alpha_management.campaign import _snapshot
    research_spec = spec(settings=replace(settings(), universe="synthetic-subset"))
    _, sessions = nonmember_sessions(tmp_path, research_spec)
    run = run_expression_research(research_spec, sessions)
    rebuilt = []
    for index, coordinates in enumerate(run.iter_session_inputs()):
        assert coordinates["instrument_ids"] == list(IDS[:1])
        assert coordinates["resolver_instrument_ids"] == list(IDS)
        context = AsOfContext(**{
            **coordinates["context"],
            "as_of_utc": datetime.fromisoformat(coordinates["context"]["as_of_utc"]),
            "information_cutoff_utc": datetime.fromisoformat(coordinates["context"]["information_cutoff_utc"]),
        })
        periods = tuple(coordinates["reference_periods"])
        resolver = RepositoryPanelResolver(
            sessions[index].resolver.fields,
            tuple(coordinates["resolver_instrument_ids"]), context, {}, periods,
            {period: frozenset(members) for period, members in zip(periods, coordinates["membership"])},
        )
        restored = HistoricalSession(
            sessions[index].effective_time_utc, context, resolver,
            tuple(coordinates["instrument_ids"]),
            universe_version=coordinates["universe_version"],
        )
        rebuilt.append(restored)
        assert _snapshot(research_spec, restored, rebuilt)[1] == run.payload()["session_input_hashes"][index]
    assert run_expression_research(research_spec, rebuilt).evidence_hash == run.evidence_hash


# Codex PR #78 R2: validation must cover every historical period, including
# warm-up, rather than validating only the simulation's current classifications.
@pytest.mark.parametrize("operator", ["group_rank", "group_neutralize"])
@pytest.mark.parametrize("bad", [None, "", "  ", 42, False, "__absent__"])
@pytest.mark.parametrize("period_index", [0, 3])
def test_expression_group_panels_reject_incomplete_classification(tmp_path, operator, bad, period_index):
    research_spec = replace(spec(),
        expression=f"{operator}(ts_return(total_return_index,2),sector)",
        group_fields=("sector",))
    source, sessions = repository_sessions(tmp_path, research_spec)
    append_group_evidence(source, sessions)
    prepared = []
    for session in sessions:
        history = {p: dict.fromkeys(IDS, "industry") for p in session.resolver.reference_periods}
        if len(history) > period_index:
            period = session.resolver.reference_periods[period_index]
            if bad == "__absent__":
                history[period].pop(IDS[1])
            else:
                history[period][IDS[1]] = bad
        prepared.append(replace(session, resolver=replace(session.resolver, groups={"sector": history})))
    with pytest.raises(ValueError, match="expression group"):
        run_expression_research(research_spec, prepared)


@pytest.mark.parametrize("operator", ["group_rank", "group_neutralize"])
def test_complete_expression_groups_keep_canonical_semantics(tmp_path, operator):
    research_spec = replace(spec(),
        expression=f"{operator}(ts_return(total_return_index,2),sector)",
        group_fields=("sector",))
    source, sessions = repository_sessions(tmp_path, research_spec)
    append_group_evidence(source, sessions)
    prepared = [replace(session, resolver=replace(session.resolver, groups={"sector": {
        period: dict.fromkeys(IDS, "industry") for period in session.resolver.reference_periods
    }})) for session in sessions]
    run = run_expression_research(research_spec, prepared)
    assert run.result == simulate_history(research_spec.compiled, prepared, research_spec.settings)


def test_expression_group_validation_does_not_require_nonmembers(tmp_path):
    research_spec = replace(spec(settings=replace(settings(), universe="synthetic-subset")),
        expression="group_rank(ts_return(total_return_index,2),sector)", group_fields=("sector",))
    source, sessions = nonmember_sessions(tmp_path, research_spec)
    append_group_evidence(source, sessions)
    prepared = [replace(session, resolver=replace(session.resolver, groups={"sector": {
        period: {IDS[0]: "industry"} for period in session.resolver.reference_periods
    }})) for session in sessions]
    run = run_expression_research(research_spec, prepared)
    assert run.payload()["result_status"] == "COMPUTED"


def replay_from_receipt(run):
    """Replay consumed snapshots, not mutable latest-vintage repository queries."""
    from types import MappingProxyType
    from alpha_management.campaign import _SnapshotResolver, _hash
    payload = run.payload()
    sessions = []
    for index, coordinates in enumerate(run.iter_session_inputs()):
        assert _hash(coordinates) == payload['session_input_hashes'][index]
        axis = coordinates['resolver_instrument_ids']
        resolver = _SnapshotResolver(
            {name: {instrument: tuple(panel[instrument]) for instrument in axis}
             for name, panel in coordinates['fields'].items()},
            {name: {instrument: tuple(panel[instrument]) for instrument in axis}
             for name, panel in coordinates['groups'].items()},
            tuple(frozenset(members) for members in coordinates['membership']),
        )
        context_data = dict(coordinates['context'])
        for field in ('as_of_utc', 'information_cutoff_utc'):
            context_data[field] = datetime.fromisoformat(context_data[field])
        context = AsOfContext(**context_data)
        sessions.append(HistoricalSession(
            datetime.fromisoformat(coordinates['effective_time_utc']), context, resolver,
            tuple(coordinates['instrument_ids']),
            dataset_manifest_ids=tuple(coordinates['dataset_manifest_ids']),
            universe_version=coordinates['universe_version'],
            neutralization_groups=coordinates['neutralization_groups'],
        ))
    return sessions


def test_receipt_keeps_separate_session_order_and_effective_time(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    # The session order need not be the same as the resolver order.
    sessions = [replace(session, instrument_ids=IDS[::-1])
                for session in sessions]
    run = run_expression_research(research_spec, sessions)
    assert list(run.iter_session_inputs())[-1]['instrument_ids'] == list(IDS[::-1])
    assert list(run.iter_session_inputs())[-1]['resolver_instrument_ids'] == list(IDS)
    restored = replay_from_receipt(run)
    assert simulate_history(research_spec.compiled, restored, research_spec.settings) == run.result


def test_receipt_replays_values_after_valid_backdated_repository_append(tmp_path):
    from asset_management.data.repositories import SQLiteTemporalObservationStore
    research_spec = spec()
    source, sessions = repository_sessions(tmp_path, research_spec)
    original = run_expression_research(research_spec, sessions)
    context = sessions[-1].context
    manifest_id = sessions[-1].dataset_manifest_ids[0]
    old = source.observations.series(entity_id=IDS[0], field='total_return_index',
                                    context=context, dataset_manifest_id=manifest_id)[-1]
    # This canonical append is accepted, despite occurring after the old run.
    # Its declared timestamps fall inside that run's historical cutoff.
    revised = old.available_at + timedelta(seconds=1)
    writer = SQLiteTemporalObservationStore(source.observations._conn)
    writer.append(observation_id='late-import-vintage', entity_id=old.entity_id,
        field=old.field, value='1000', reference_period=old.reference_period,
        event_time=old.event_time, scheduled_release_at=None, official_release_at=None,
        source_timestamp=revised, received_at=revised, available_at=revised,
        ingested_at=revised, revised_at=revised, supersedes_observation_id=old.observation_id,
        source_timezone='UTC', schema_version=old.schema_version, dataset_manifest_id=manifest_id)
    current = run_expression_research(research_spec, sessions)
    assert current.payload()['session_input_hashes'][-1] != original.payload()['session_input_hashes'][-1]
    restored = replay_from_receipt(original)
    assert simulate_history(research_spec.compiled, restored, research_spec.settings) == original.result


def test_explicit_universe_version_must_match_canonical_membership(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    forged = replace(sessions[-1], universe_version='approved-universe-v1')
    with pytest.raises(DataQualityError, match='UNIVERSE_VERSION_PROVENANCE_MISMATCH'):
        run_expression_research(research_spec, [forged])


def test_research_run_rejects_result_from_different_receipt(tmp_path):
    research_spec = spec()
    _, first = repository_sessions(tmp_path/'a', research_spec)
    _, second = repository_sessions(tmp_path/'b', research_spec,
        values=((200,204,220,240),(1000,1010,1040,1050)))
    run_a = run_expression_research(research_spec, first)
    run_b = run_expression_research(research_spec, second)
    with pytest.raises(ValueError, match='result/evidence mismatch'):
        ResearchRun(run_b.result, run_a.evidence_json)