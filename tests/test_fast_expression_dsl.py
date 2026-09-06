from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from alpha_management import (
    Alpha,
    AlphaSimulationSettings,
    ExpressionError,
    HistoricalSession,
    RepositoryDataFields,
    RepositoryPanelResolver,
    compile_expression,
    simulate_cross_section,
    simulate_history,
)
from asset_management.time.asof import AsOfContext
from asset_management.domain.errors import TemporalViolation


@dataclass
class Resolver:
    panels: dict[str, dict[str, list[float | None]]]
    groups: dict[str, dict[str, str]] | None = None

    def field(self, name):
        return self.panels[name]

    def group(self, name):
        return (self.groups or {})[name]


def context(day: int) -> AsOfContext:
    moment = datetime(2026, 1, day, 21, tzinfo=timezone.utc)
    return AsOfContext(
        f"run-{day}", moment, moment - timedelta(minutes=5),
        "policy-v1", "parameters-v1", f"revision-{day}",
    )


def test_nested_expression_has_deterministic_identity_and_axis_evaluation():
    compact = compile_expression("rank(ts_delta(close, 2))", data_fields={"close"})
    spaced = compile_expression(" RANK ( ts_delta ( close , 02 ) ) ", data_fields={"close"})
    assert compact.canonical == "rank(ts_delta(close,2))"
    assert compact.expression_hash == spaced.expression_hash
    result = compact.evaluate(Resolver({"close": {"a": [1, 2, 4], "b": [3, 2, 1]}}))
    assert result == {"a": [None, None, 1.0], "b": [None, None, 0.0]}


@pytest.mark.parametrize(
    "source,message",
    [
        ("mystery(close)", "unknown operator"),
        ("rank(open)", "unknown datafield"),
        ("rank(close, 2)", "expects 1 arguments"),
        ("rank()", "expects 1 arguments"),
        ("ts_delta(close, 0)", "positive integer"),
        ("ts_delta(close, 1.5)", "expects ['Panel', 'Integer']"),
        ("group_rank(close, close)", "GroupField"),
        ("ts_stddev(close, 1)", "at least 2"),
        ("ts_decay_linear(close, 100000000000)", "cannot exceed 10000"),
    ],
)
def test_invalid_expressions_fail_closed(source, message):
    with pytest.raises(ExpressionError, match=message.replace("[", r"\[").replace("]", r"\]")):
        compile_expression(source, data_fields={"close"}, group_fields={"sector"})


def test_group_field_is_typed_and_resolved_separately():
    expression = compile_expression(
        "group_rank(close, sector)", data_fields={"close"}, group_fields={"sector"}
    )
    result = expression.evaluate(Resolver(
        {"close": {"a": [1, 4], "b": [2, 3], "c": [9, 8]}},
        {"sector": {"a": "x", "b": "x", "c": "y"}},
    ))
    assert result == {"a": [0.0, 1.0], "b": [1.0, 0.0], "c": [0.5, 0.5]}


def test_repository_panel_resolver_uses_explicit_pit_context():
    calls = []

    class Source:
        def time_series_observations(self, field, *, instrument_id, context):
            calls.append((field, instrument_id, context.information_cutoff_utc))
            return {
                "a": [("2026-01-01", 1), ("2026-01-02", 2), ("2026-01-03", 4)],
                "b": [("2026-01-01", 4), ("2026-01-02", 3), ("2026-01-03", 1)],
            }[instrument_id]

        def time_series(self, field, *, instrument_id, context):
            raise AssertionError("aligned resolver must retain reference periods")

        def cross_section(self, field, *, universe, context):
            raise AssertionError("panel expressions must use historical series")

    ctx = context(1)
    resolver = RepositoryPanelResolver(
        fields=RepositoryDataFields(Source()),
        instrument_ids=("a", "b"),
        context=ctx,
        groups={},
        reference_periods=("2026-01-01", "2026-01-02", "2026-01-03"),
        universe_membership={
            period: frozenset({"a", "b"})
            for period in ("2026-01-01", "2026-01-02", "2026-01-03")
        },
    )
    expression = compile_expression("rank(ts_delta(close, 2))", data_fields={"close"})
    assert expression.evaluate(resolver) == {
        "a": [None, None, 1.0],
        "b": [None, None, 0.0],
    }
    assert calls == [
        ("close", "a", ctx.information_cutoff_utc),
        ("close", "b", ctx.information_cutoff_utc),
    ]


def test_repository_panel_aligns_uneven_histories_by_reference_period():
    class Source:
        def time_series_observations(self, field, *, instrument_id, context):
            return {
                "a": [("2026-01-01", 1), ("2026-01-03", 3)],
                "b": [("2026-01-02", 2), ("2026-01-03", 4)],
            }[instrument_id]

        def time_series(self, field, *, instrument_id, context):
            raise AssertionError

        def cross_section(self, field, *, universe, context):
            raise AssertionError

    ctx = context(1)
    resolver = RepositoryPanelResolver(
        RepositoryDataFields(Source()),
        ("a", "b"),
        ctx,
        {},
        ("2026-01-01", "2026-01-02", "2026-01-03"),
        {
            period: frozenset({"a", "b"})
            for period in ("2026-01-01", "2026-01-02", "2026-01-03")
        },
    )
    assert resolver.field("close") == {
        "a": [1.0, None, 3.0],
        "b": [None, 2.0, 4.0],
    }


def test_history_rejects_repository_resolver_from_another_cutoff():
    class Source:
        def time_series_observations(self, field, *, instrument_id, context):
            return [("2026-01-01", 1)]

        def time_series(self, field, *, instrument_id, context):
            return [1]

        def cross_section(self, field, *, universe, context):
            return {"a": 1}

    earlier = context(1)
    later = context(2)
    resolver = RepositoryPanelResolver(
        RepositoryDataFields(Source()),
        ("a",),
        later,
        {},
        ("2026-01-01",),
        {"2026-01-01": frozenset({"a"})},
    )
    with pytest.raises(ValueError, match="resolver context must match"):
        HistoricalSession(earlier.as_of_utc, earlier, resolver, ("a",))


def test_callable_alpha_api_remains_compatible():
    alpha = Alpha("callable", lambda values: values["signal"])
    settings = AlphaSimulationSettings(
        universe="test", delay=0, neutralization="none", truncation=1, long_only=False
    )
    result = simulate_cross_section(alpha, {"signal": {"a": 2.0, "b": -1.0}}, settings)
    assert result.weights == {"a": pytest.approx(2 / 3), "b": pytest.approx(-1 / 3)}


def sessions(values):
    result = []
    for day, cross_section in enumerate(values, start=1):
        ctx = context(day)
        result.append(HistoricalSession(
            effective_time_utc=ctx.as_of_utc,
            context=ctx,
            resolver=Resolver({"close": {
                instrument: [value] for instrument, value in cross_section.items()
            }}),
            instrument_ids=tuple(cross_section),
            dataset_manifest_ids=(f"manifest-{day}",),
            universe_version="universe-v1",
        ))
    return result


def history_settings(*, delay=0, decay=0):
    return AlphaSimulationSettings(
        universe="test", delay=delay, decay=decay, neutralization="none",
        truncation=1, long_only=False,
    )


def test_history_delay_uses_prior_trading_session_and_preserves_lineage():
    expression = compile_expression("sign(close)", data_fields={"close"})
    result = simulate_history(
        expression,
        sessions([
            {"a": 2, "b": -1},
            {"a": -4, "b": 3},
            {"a": 8, "b": -2},
        ]),
        history_settings(delay=1),
    )
    assert result.position_panel == {
        "a": [None, 0.5, -0.5],
        "b": [None, -0.5, 0.5],
    }
    assert result.points[0].signal_time_utc is None
    assert result.points[1].signal_time_utc == context(1).as_of_utc
    assert result.points[1].information_cutoff_utc == context(1).information_cutoff_utc
    assert result.points[1].dataset_manifest_ids == ("manifest-1",)
    assert result.points[1].signal_universe_version == "universe-v1"
    assert result.points[1].source_run_id == "run-1"
    assert result.points[1].code_revision == "revision-1"
    assert result.points[1].neutralization_groups == {}
    assert result.expression_hash == expression.expression_hash


def test_history_delay_zero_and_general_integer():
    expression = compile_expression("sign(close)", data_fields={"close"})
    source = sessions([{"a": 1}, {"a": -1}, {"a": 1}])
    immediate = simulate_history(expression, source, history_settings(delay=0))
    delayed = simulate_history(expression, source, history_settings(delay=2))
    assert immediate.position_panel["a"] == [1.0, -1.0, 1.0]
    assert delayed.position_panel["a"] == [None, None, 1.0]


def test_position_decay_is_after_cross_section_and_requires_full_window():
    expression = compile_expression("sign(close)", data_fields={"close"})
    result = simulate_history(
        expression,
        sessions([
            {"a": 1, "b": -1},
            {"a": -1, "b": 1},
            {"a": 1, "b": -1},
            {"a": 1, "b": -1},
        ]),
        history_settings(decay=3),
    )
    assert result.position_panel["a"][:2] == [None, None]
    assert result.position_panel["a"][2] == pytest.approx(1 / 6)
    assert result.position_panel["a"][3] == pytest.approx(1 / 3)
    assert result.position_panel["b"][2] == pytest.approx(-1 / 6)


def test_expression_decay_and_simulation_decay_are_independent():
    expression = compile_expression("ts_decay_linear(close, 2)", data_fields={"close"})
    feature = expression.evaluate(Resolver({"close": {"a": [1, 2, 3]}}))
    assert feature["a"] == [None, pytest.approx(5 / 3), pytest.approx(8 / 3)]
    result = simulate_history(
        compile_expression("sign(close)", data_fields={"close"}),
        sessions([{"a": 1}, {"a": -1}]),
        history_settings(decay=0),
    )
    assert result.settings.decay == 0
    assert result.position_panel["a"] == [1.0, -1.0]


def test_history_delay_zero_matches_cross_section_and_long_only_stays_a_setting():
    expression = compile_expression("close", data_fields={"close"})
    timeline = sessions([{"a": 2, "b": -1}])
    research = simulate_history(expression, timeline, history_settings(delay=0))
    direct = simulate_cross_section(
        Alpha("close", lambda _context: {"a": 2.0, "b": -1.0}),
        {},
        history_settings(delay=0),
    )
    assert research.points[0].base_weights == direct.weights
    live_shaped = simulate_history(
        expression,
        timeline,
        AlphaSimulationSettings(
            universe="test", delay=0, neutralization="none",
            truncation=1, long_only=True,
        ),
    )
    assert research.position_panel["b"] == [pytest.approx(-1 / 3)]
    assert live_shaped.position_panel == {"a": [1.0], "b": [0.0]}


def test_history_rejects_information_after_source_cutoff():
    ctx = context(1)

    class FutureSource:
        def time_series_observations(self, field, *, instrument_id, context):
            context.require_known_at(
                context.information_cutoff_utc + timedelta(seconds=1),
                label="future close",
            )

        def time_series(self, field, *, instrument_id, context):
            raise AssertionError

        def cross_section(self, field, *, universe, context):
            raise AssertionError

    resolver = RepositoryPanelResolver(
        RepositoryDataFields(FutureSource()),
        ("a",),
        ctx,
        {},
        ("2026-01-01",),
        {"2026-01-01": frozenset({"a"})},
    )
    session = HistoricalSession(ctx.as_of_utc, ctx, resolver, ("a",))
    with pytest.raises(TemporalViolation, match="after information_cutoff_utc"):
        simulate_history(
            compile_expression("close", data_fields={"close"}),
            [session],
            history_settings(),
        )


def test_history_can_attach_existing_metric_bundle():
    result = simulate_history(
        compile_expression("sign(close)", data_fields={"close"}),
        sessions([{"a": 1}, {"a": 1}, {"a": -1}]),
        history_settings(delay=1),
        forward_returns={"a": [0.01, -0.02, 0.03]},
    )
    assert result.metrics is not None
    assert result.metrics.periods == 2


def test_repository_cross_sections_use_historical_universe_membership():
    class Source:
        def time_series_observations(self, field, *, instrument_id, context):
            return [("p1", 1 if instrument_id == "a" else 9),
                    ("p2", 2 if instrument_id == "a" else 3)]

        def time_series(self, field, *, instrument_id, context):
            raise AssertionError

        def cross_section(self, field, *, universe, context):
            raise AssertionError

    ctx = context(1)
    resolver = RepositoryPanelResolver(
        RepositoryDataFields(Source()),
        ("a", "b"),
        ctx,
        {},
        ("p1", "p2"),
        {"p1": frozenset({"a"}), "p2": frozenset({"a", "b"})},
    )
    assert resolver.field("close")["b"] == [None, 3.0]
    result = compile_expression("rank(close)", data_fields={"close"}).evaluate(resolver)
    assert result == {"a": [0.5, 0.0], "b": [None, 1.0]}
    nested = compile_expression("ts_mean(rank(close), 2)", data_fields={"close"})
    assert nested.evaluate(resolver) == {"a": [None, 0.25], "b": [None, None]}


def test_repository_group_operators_use_historical_classifications():
    class Source:
        def time_series_observations(self, field, *, instrument_id, context):
            values = {"a": [1, 1], "b": [3, 4], "c": [8, 9]}
            return list(zip(("p1", "p2"), values[instrument_id]))

        def time_series(self, field, *, instrument_id, context):
            raise AssertionError

        def cross_section(self, field, *, universe, context):
            raise AssertionError

    ctx = context(1)
    resolver = RepositoryPanelResolver(
        RepositoryDataFields(Source()),
        ("a", "b", "c"),
        ctx,
        {"sector": {
            "p1": {"a": "x", "b": "x", "c": "y"},
            "p2": {"a": "x", "b": "y", "c": "y"},
        }},
        ("p1", "p2"),
        {"p1": frozenset({"a", "b", "c"}), "p2": frozenset({"a", "b", "c"})},
    )
    result = compile_expression(
        "group_rank(close, sector)", data_fields={"close"}, group_fields={"sector"}
    ).evaluate(resolver)
    assert result == {"a": [0.0, 0.5], "b": [1.0, 0.0], "c": [0.5, 1.0]}


def test_history_group_neutralization_uses_delayed_session_groups():
    expression = compile_expression("close", data_fields={"close"})
    source = sessions([
        {"a": 1, "b": 3, "c": 10},
        {"a": 9, "b": 2, "c": 1},
    ])
    source[0] = HistoricalSession(
        effective_time_utc=source[0].effective_time_utc,
        context=source[0].context,
        resolver=source[0].resolver,
        instrument_ids=source[0].instrument_ids,
        dataset_manifest_ids=source[0].dataset_manifest_ids,
        universe_version=source[0].universe_version,
        neutralization_groups={"a": "x", "b": "x", "c": "y"},
    )
    settings = AlphaSimulationSettings(
        universe="test", delay=1, neutralization="group",
        truncation=1, long_only=False,
    )
    result = simulate_history(expression, source, settings)
    assert result.position_panel == {
        "a": [None, -0.5],
        "b": [None, 0.5],
        "c": [None, 0.0],
    }
