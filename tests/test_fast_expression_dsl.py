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
        def time_series(self, field, *, instrument_id, context):
            calls.append((field, instrument_id, context.information_cutoff_utc))
            return {"a": [1, 2, 4], "b": [4, 3, 1]}[instrument_id]

        def cross_section(self, field, *, universe, context):
            raise AssertionError("panel expressions must use historical series")

    ctx = context(1)
    resolver = RepositoryPanelResolver(
        fields=RepositoryDataFields(Source()),
        instrument_ids=("a", "b"),
        context=ctx,
        groups={},
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
    assert result.points[1].source_run_id == "run-1"
    assert result.points[1].code_revision == "revision-1"
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
