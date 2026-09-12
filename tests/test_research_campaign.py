"""Synthetic mechanism evidence: real immutable/PIT stores, no market claims."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from alpha_management.campaign import ResearchSpec, ResearchTheme, run_expression_research
from alpha_management.quant import momentum_spec
from alpha_management import (
    AlphaSimulationSettings, HistoricalSession, PointInTimeDataSource,
    RepositoryDataFields, RepositoryPanelResolver, compile_expression, simulate_history,
)
from alpha_management.operators import ts_return
from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.data.asof_query import AsOfRepository
from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.data.repositories import SQLiteTemporalObservationStore
from asset_management.reference.universe import UniverseRepository
from asset_management.time.asof import AsOfContext
from asset_management.time.clock import FrozenClock

ROOT = Path(__file__).parents[1]
T = datetime(2026, 1, 5, 21, tzinfo=timezone.utc)
IDS = ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002")
LICENSE = "purpose=synthetic-research;redistribution=forbidden;retention=perpetual"


def settings(**kwargs):
    return AlphaSimulationSettings(universe="synthetic", neutralization="none", truncation=1.0, **kwargs)


def spec(**kwargs):
    values = dict(field="total_return_index", input_contract_key="synthetic-total-return-index-v1",
                  lookback=2, settings=settings(), evaluation_horizon_sessions=5,
                  policy_version="research-v1", dataset_source="synthetic", dataset_name="index",
                  dataset_schema_version="synthetic-total-return-index-v1")
    values.update(kwargs)
    return momentum_spec(**values)


def ctx(day, research_spec):
    moment = T + timedelta(days=day)
    return AsOfContext(f"synthetic-{day}", moment, moment - timedelta(minutes=5),
                       research_spec.policy_version, research_spec.spec_hash, "revision-test")


def repository_sessions(tmp_path, research_spec, *, values=None):
    """Register real append-only observations under each immutable snapshot."""
    values = values or ((100, 102, 110, 120), (1000, 1010, 1040, 1050))
    conn = sqlite3.connect(":memory:")
    Migrator(conn, FrozenClock(T)).migrate(load_migration_catalog(ROOT / "schemas"))
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    universes = UniverseRepository(conn)
    start = T - timedelta(days=2)
    for index, identifier in enumerate(IDS):
        universes.register(instrument_id=identifier, ticker=f"SYNTH-{index}",
            toss_symbol=f"SYNTH-{index}", vendor_symbol=f"SYNTH-{index}", cik=None,
            mic="XNYS", asset_class="ETF", currency="USD", timezone="UTC",
            effective_from=start, available_at=start, source="synthetic")
        universes.include(membership_id=f"membership-{index}", universe_id="synthetic",
            instrument_id=identifier, inclusion_reason="synthetic fixture",
            effective_from=start, available_at=start, source="synthetic")
    writer = SQLiteTemporalObservationStore(conn)
    source = PointInTimeDataSource(AsOfRepository(conn), store, universes, "synthetic", "index")
    sessions, previous = [], {}
    for day in range(len(values[0])):
        context = ctx(day, research_spec)
        known = context.information_cutoff_utc - timedelta(minutes=1)
        stamp = known.isoformat()
        rows = [{"entity": identifier, "period": (T + timedelta(days=j)).date().isoformat(),
                 "total_return_index": str(values[i][j])}
                for i, identifier in enumerate(IDS) for j in range(day + 1)
                if values[i][j] is not None]
        metadata = dict(source="synthetic", dataset="index",
            schema_version="synthetic-total-return-index-v1", retrieved_at=known,
            available_at=known, provider_timestamp=known, license_tag=LICENSE,
            code_revision="revision-test", request_hash=sha256(str(day).encode()).hexdigest())
        bronze = store.write(rows, layer="bronze", **metadata)
        silver = store.write(rows, layer="silver", parent_manifest_ids=(bronze.manifest_id,), **metadata)
        conn.execute("INSERT INTO am_runtime_run VALUES (?,?,?,?,?)",
                     (f"ingest-{day}", stamp, stamp, "revision-test", stamp))
        conn.execute("INSERT INTO am_ingestion_run VALUES (?,?,?,?,?)",
                     (f"ingest-{day}", f"ingest-{day}", "synthetic", stamp, stamp))
        conn.execute("INSERT INTO am_dataset_manifest VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (silver.manifest_id, f"ingest-{day}", "silver", "index", "fixture://index",
                      silver.content_sha256, stamp, stamp, silver.schema_version, len(rows)))
        conn.commit()
        for row in rows:
            key = (row["entity"], row["period"])
            prior = previous.get(key)
            observation = writer.append(
                observation_id=f"obs-{day}-{row['entity']}-{row['period']}",
                entity_id=row["entity"], field="total_return_index", value=row["total_return_index"],
                reference_period=row["period"],
                event_time=datetime.fromisoformat(row["period"]).replace(hour=20, tzinfo=timezone.utc),
                scheduled_release_at=None, official_release_at=None,
                source_timestamp=known, received_at=known, available_at=known, ingested_at=known,
                revised_at=known if prior else None, supersedes_observation_id=prior,
                source_timezone="UTC", schema_version=silver.schema_version,
                dataset_manifest_id=silver.manifest_id,
            )
            previous[key] = observation.observation_id
        periods = tuple((T + timedelta(days=j)).date().isoformat() for j in range(day + 1))
        resolver = RepositoryPanelResolver(
            RepositoryDataFields(source), IDS, context, {}, periods,
            {period: frozenset(IDS) for period in periods},
        )
        sessions.append(HistoricalSession(context.as_of_utc, context, resolver, IDS))
    return source, sessions


def test_return_is_not_price_difference_and_is_scale_invariant():
    left = ts_return([100, 105, 120], 2)
    right = ts_return([10000, 10500, 12000], 2)
    assert left == right
    assert left[:2] == [None, None]
    assert left[2] == pytest.approx(0.2)


@pytest.mark.parametrize("lag", [True, False, 0, -1, 1.5, "2", None])
def test_return_rejects_invalid_lag(lag):
    with pytest.raises(ValueError, match="positive integer"):
        ts_return([1, 2], lag)


@pytest.mark.parametrize("bad", [True, False, 0, -1, float("nan"), float("inf"), -float("inf"), "bad"])
def test_return_rejects_invalid_inputs_even_in_warmup(bad):
    with pytest.raises(ValueError, match="positive finite"):
        ts_return([1, bad], 10)


def test_return_missing_is_not_zero_or_a_shorter_holding_period():
    assert ts_return([100, None, 110, 120, 130], 2)[:4] == [None] * 4
    assert ts_return([100, None, 110, 120, 130], 2)[-1] == pytest.approx(130 / 110 - 1)
    assert ts_return([1, 1], 1) == [None, 0.0]
    assert ts_return([], 2) == []


def test_return_rejects_overflow():
    with pytest.raises(ValueError, match="output must be finite"):
        ts_return([1e-308, 1e308], 1)


@pytest.mark.parametrize("expression", ["ts_return(x,0)", "ts_return(x,1.1)", "ts_return(x,10001)", "x / x"])
def test_dsl_rejects_unapproved_syntax_and_lags(expression):
    with pytest.raises(ValueError):
        compile_expression(expression, data_fields={"x"})


def test_dsl_return_uses_canonical_operator_and_rejects_booleans():
    from types import SimpleNamespace
    expression = compile_expression("rank(ts_return(x,2))", data_fields={"x"})
    values = {"a": [10, 11, 12], "b": [1000, 1010, 1020]}
    assert expression.evaluate(SimpleNamespace(field=lambda _: values)) == {
        "a": [None, None, 1.0], "b": [None, None, 0.0]}
    with pytest.raises(ValueError, match="boolean"):
        expression.evaluate(SimpleNamespace(field=lambda _: {"a": [True, 2, 3]}))


def test_spec_identity_canonicalization_and_defensive_copy():
    original = spec()
    contracts = dict(original.field_contracts)
    criteria = list(original.falsification_criteria)
    copy = replace(original, expression=" RANK ( ts_return(total_return_index, 02) ) ",
                   field_contracts=contracts, falsification_criteria=criteria)
    assert copy.spec_hash == original.spec_hash
    contracts["total_return_index"] = "different"
    criteria.append("mutation")
    assert copy.spec_hash == original.spec_hash
    assert replace(original, thesis="Other hypothesis").spec_hash != original.spec_hash
    assert replace(original, evaluation_horizon_sessions=21).spec_hash != original.spec_hash
    assert spec(lookback=3).spec_hash != original.spec_hash
    assert spec(settings=settings(delay=2)).spec_hash != original.spec_hash
    with pytest.raises(TypeError):
        original.field_contracts["total_return_index"] = "mutation"
    with pytest.raises(FrozenInstanceError):
        original.family = "other"


@pytest.mark.parametrize("changes", [
    {"theme": "unknown"}, {"family": " "}, {"policy_version": ""},
    {"falsification_criteria": ()}, {"falsification_criteria": "not a sequence"},
    {"evaluation_horizon_sessions": True}, {"evaluation_horizon_sessions": 0},
    {"field_contracts": {}}, {"field_contracts": {"total_return_index": ""}},
    {"field_contracts": {"total_return_index": "v1", "unused": "v1"}},
    {"group_fields": ("unused_group",)}, {"group_fields": "sector"},
])
def test_spec_rejects_underspecified_contracts(changes):
    with pytest.raises((ValueError, TypeError)):
        replace(spec(), **changes)


@pytest.mark.parametrize("changes", [
    {"delay": True}, {"decay": 1.5}, {"book_size": float("inf")},
    {"book_size": True}, {"long_only": "true"},
])
def test_spec_does_not_inherit_permissive_settings(changes):
    with pytest.raises(ValueError):
        spec(settings=settings(**changes))


def test_actual_repository_to_canonical_simulator_produces_deterministic_receipt(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    result = run_expression_research(research_spec, sessions)
    again = run_expression_research(research_spec, sessions)
    assert result.evidence_hash == again.evidence_hash
    assert result.result == simulate_history(research_spec.compiled, sessions, research_spec.settings)
    assert result.result.points[0].raw == dict.fromkeys(IDS)
    assert result.result.points[-1].raw == {IDS[0]: 1.0, IDS[1]: 0.0}
    assert result.result.metrics is None
    payload = result.payload()
    assert payload["validation_scope"] == "MECHANISM_ONLY"
    assert payload["result_status"] == "COMPUTED"
    assert payload["spec"]["return_basis"] == "NOT_A_RETURN"
    assert payload["spec_hash"] == research_spec.spec_hash
    assert len(payload["session_input_hashes"]) == 4
    assert len(set(payload["session_input_hashes"])) == 4
    payload["result"]["points"] = []
    assert len(result.payload()["result"]["points"]) == 4
    assert not hasattr(result, "forecast") and not hasattr(result, "order")


def test_input_hash_changes_even_when_rank_does_not(tmp_path):
    research_spec = spec()
    _, first = repository_sessions(tmp_path / "a", research_spec)
    _, second = repository_sessions(tmp_path / "b", research_spec,
        values=((200, 204, 220, 240), (1000, 1010, 1040, 1050)))
    a, b = (run_expression_research(research_spec, values) for values in (first, second))
    assert a.result.points[-1].raw == b.result.points[-1].raw
    assert a.evidence_hash != b.evidence_hash
    assert a.payload()["session_input_hashes"] != b.payload()["session_input_hashes"]


def test_warmup_only_is_explicitly_unavailable(tmp_path):
    research_spec = spec(lookback=10)
    _, sessions = repository_sessions(tmp_path, research_spec)
    run = run_expression_research(research_spec, sessions)
    assert run.payload()["result_status"] == "NO_OBSERVATIONS"
    assert all(all(value is None for value in p.raw.values()) for p in run.result.points)


def test_session_order_and_spec_substitution_fail_before_simulation(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    for invalid in ([], sessions[::-1], [sessions[0], sessions[0]]):
        with pytest.raises(ValueError):
            run_expression_research(research_spec, invalid)
    with pytest.raises(ValueError, match="bound to the session"):
        run_expression_research(spec(lookback=3), sessions)
    changed = replace(sessions[-1].context, code_revision="different")
    last_resolver = replace(sessions[-1].resolver, context=changed)
    last = replace(sessions[-1], context=changed, resolver=last_resolver)
    with pytest.raises(ValueError, match="one code revision"):
        run_expression_research(research_spec, [*sessions[:-1], last])


def test_unverified_resolver_cannot_claim_repository_lineage(tmp_path):
    from types import SimpleNamespace
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    fake = replace(sessions[0], resolver=SimpleNamespace(field=lambda _: {IDS[0]: [1]}))
    with pytest.raises(ValueError, match="canonical repository"):
        run_expression_research(research_spec, [fake])


def test_canonical_universe_cannot_be_replaced_by_only_current_winner(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    original = sessions[-1]
    periods = original.resolver.reference_periods
    resolver = replace(original.resolver, instrument_ids=(IDS[0],),
                       universe_membership={period: frozenset({IDS[0]}) for period in periods})
    narrowed = replace(original, resolver=resolver, instrument_ids=(IDS[0],))
    with pytest.raises(ValueError, match="canonical reference truth"):
        run_expression_research(research_spec, [narrowed])


def test_future_period_axis_is_not_accepted_as_current_score(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    original = sessions[-1]
    periods = (*original.resolver.reference_periods, "2099-01-01")
    resolver = replace(original.resolver, reference_periods=periods,
        universe_membership={period: frozenset(IDS) for period in periods})
    invalid = replace(original, resolver=resolver)
    with pytest.raises(ValueError, match="after effective time"):
        run_expression_research(research_spec, [invalid])


def test_pinned_manifest_ignores_later_catalog_and_detects_tampering(tmp_path):
    research_spec = spec()
    source, sessions = repository_sessions(tmp_path, research_spec)
    result = run_expression_research(research_spec, sessions[:3])
    # The fourth manifest exists but did not exist at the third session cutoff.
    assert sessions[2].dataset_manifest_ids != sessions[3].dataset_manifest_ids
    assert result.evidence_hash == run_expression_research(research_spec, sessions[:3]).evidence_hash
    identifier = sessions[2].dataset_manifest_ids[0]
    manifest, _ = source.datasets.read(identifier)
    path = source.datasets.layout.resolve("silver", f"{manifest.content_sha256}.json")
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        run_expression_research(research_spec, sessions[:3])


def test_past_membership_cannot_drop_a_loser_while_current_universe_matches(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    original = sessions[-1]
    membership = dict(original.resolver.universe_membership)
    membership[original.resolver.reference_periods[0]] = frozenset({IDS[0]})
    resolver = replace(original.resolver, universe_membership=membership)
    invalid = replace(original, resolver=resolver)
    with pytest.raises(ValueError, match="historical membership"):
        run_expression_research(research_spec, [*sessions[:-1], invalid])


def test_omitting_a_warmup_session_cannot_shorten_lag(tmp_path):
    research_spec = spec()
    _, sessions = repository_sessions(tmp_path, research_spec)
    with pytest.raises(ValueError, match="complete warm-up"):
        run_expression_research(research_spec, sessions[1:])


def test_valid_manifest_from_wrong_dataset_schema_fails_closed(tmp_path):
    research_spec = spec(dataset_schema_version="raw-price-v1")
    _, sessions = repository_sessions(tmp_path, research_spec)
    with pytest.raises(ValueError, match="dataset contract mismatch"):
        run_expression_research(research_spec, sessions)


def test_declared_contract_change_changes_spec_identity():
    original = spec()
    assert original.spec_hash != spec(dataset_source="other").spec_hash
    assert original.spec_hash != spec(dataset_schema_version="other").spec_hash


@pytest.mark.parametrize("delay,decay", [(0, 0), (1, 0), (1, 2), (2, 2)])
def test_delay_and_position_decay_keep_existing_semantics(tmp_path, delay, decay):
    research_spec = spec(settings=settings(delay=delay, decay=decay))
    _, sessions = repository_sessions(tmp_path, research_spec)
    run = run_expression_research(research_spec, sessions)
    assert run.result == simulate_history(research_spec.compiled, sessions, research_spec.settings)
    if delay:
        assert run.result.points[0].signal_time_utc is None
    if delay == 1 and decay == 2:
        # Raw scores can exist before a complete position-decay window exists.
        assert run.result.points[-1].raw[IDS[0]] == 1.0
        assert run.result.points[-1].weights[IDS[0]] is None


def test_repository_rejects_boolean_before_numeric_coercion(tmp_path):
    from alpha_management.datafields import _number
    with pytest.raises(ValueError, match="boolean"):
        _number(True)


def test_group_neutralization_requires_complete_explicit_classifications(tmp_path):
    research_spec = spec(settings=AlphaSimulationSettings(
        universe="synthetic", neutralization="group", truncation=1.0, long_only=False))
    _, sessions = repository_sessions(tmp_path, research_spec)
    with pytest.raises(ValueError, match="complete neutralization groups"):
        run_expression_research(research_spec, sessions)
    grouped = [replace(session, neutralization_groups=dict.fromkeys(IDS, "synthetic-sector"))
               for session in sessions]
    run = run_expression_research(research_spec, grouped)
    assert run.result == simulate_history(research_spec.compiled, grouped, research_spec.settings)
    assert run.result.points[-1].weights[IDS[1]] < 0


def test_receipt_preserves_contexts_for_unavailable_points(tmp_path):
    research_spec = spec(lookback=10)
    _, sessions = repository_sessions(tmp_path, research_spec)
    run = run_expression_research(research_spec, sessions)
    assert run.payload()["result_status"] == "NO_OBSERVATIONS"
    assert len(run.payload()["session_input_hashes"]) == len(sessions)
    first = run.payload()["session_inputs"][0]
    assert first["context"]["information_cutoff_utc"] == sessions[0].context.information_cutoff_utc.isoformat()
    assert first["dataset_manifest_ids"] == list(sessions[0].dataset_manifest_ids)
    assert first["reference_periods"] == list(sessions[0].resolver.reference_periods)
    assert run.result.points[-1].source_run_id == sessions[-2].context.run_id
