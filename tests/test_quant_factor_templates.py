"""Arithmetic, semantic parity and PIT integration for the six quant templates."""
from datetime import date, timedelta
from math import sin, cos
from types import SimpleNamespace

import pytest

from alpha_management.arithmetic import binary_panel
from alpha_management.campaign import run_expression_research
from alpha_management.dsl import compile_expression
from alpha_management.quant import QUANT_FAMILIES, quant_factor_spec
from test_research_campaign import IDS, repository_sessions, settings


def factor_spec(family, **overrides):
    parameters = dict(family=family, field="total_return_index", input_contract_key="synthetic-total-return-index-v1",
        settings=settings(), evaluation_horizon_sessions=5, policy_version="research-v1",
        dataset_source="synthetic", dataset_name="index", dataset_schema_version="synthetic-total-return-index-v1",
        long_window=3, short_window=2, volatility_window=2)
    if family == "multi_factor_composite":
        parameters["composite_weights"] = {"cross_sectional_momentum": 0.4, "low_volatility": 0.6}
    parameters.update(overrides)
    return quant_factor_spec(**parameters)


@pytest.mark.parametrize("name,a,b,expected", [
    ("add", {"a": [1, None]}, 2, [3, None]),
    ("subtract", 2, {"a": [1, None]}, [1, None]),
    ("multiply", {"a": [1, None]}, 0, [0, None]),
    ("divide", {"a": [1, 1]}, {"a": [0, 2]}, [None, 0.5]),
])
def test_scalar_broadcast_and_missing_contract(name, a, b, expected):
    assert binary_panel(name, a, b) == {"a": expected}


def test_binary_alignment_uses_instrument_identity_not_position():
    assert binary_panel("subtract", {"a": [3], "b": [5]}, {"b": [4], "a": [1]}) == {
        "a": [2], "b": [1]}


@pytest.mark.parametrize("left,right", [
    ({"a": [1]}, {"b": [1]}), ({"a": [1]}, {"a": [1, 2]}),
    ({"a": [1], "b": [1, 2]}, 1), (1, 2),
    ({"a": [True]}, 1), ({"a": [float("inf")]}, 1),
    ({"a": [None]}, float("nan")), ({"a": [1]}, True),
])
def test_binary_rejects_ambiguous_axes_and_non_numeric_inputs(left, right):
    with pytest.raises(ValueError):
        binary_panel("add", left, right)


def test_arithmetic_overflow_is_not_an_alpha():
    with pytest.raises(ValueError, match="finite"):
        binary_panel("multiply", {"a": [1e308]}, 1e308)
    with pytest.raises(ValueError, match="finite"):
        binary_panel("divide", {"a": [1e308]}, 1e-308)


@pytest.mark.parametrize("source", ["add(1,2)", "add(x,sector)", "negate(sector)", "add(x)", "x+x"])
def test_arithmetic_type_and_existing_call_grammar_contract(source):
    with pytest.raises(ValueError):
        compile_expression(source, data_fields={"x"}, group_fields={"sector"})


def test_nested_arithmetic_has_stable_identity_and_broadcast():
    first = compile_expression("ADD(x,2.00)", data_fields={"x"})
    second = compile_expression("add(x,2)", data_fields={"x"})
    assert first.expression_hash == second.expression_hash
    assert first.evaluate(SimpleNamespace(field=lambda _: {"a": [1, None]})) == {"a": [3, None]}
    negated = compile_expression("negate(x)", data_fields={"x"})
    assert negated.evaluate(SimpleNamespace(field=lambda _: {"a": [1, None]})) == {"a": [-1, None]}


@pytest.mark.parametrize("family", QUANT_FAMILIES)
def test_all_families_use_canonical_repository_run_contract(tmp_path, family):
    spec = factor_spec(family)
    _, sessions = repository_sessions(tmp_path, spec, values=(
        (100, 102, 110, 120, 118, 123), (1000, 1010, 1040, 1050, 1030, 1060)))
    result = run_expression_research(spec, sessions)
    assert result.payload()["result_status"] == "COMPUTED"
    assert result.result.expression_hash == spec.compiled.expression_hash
    assert all(value is not None for value in result.result.points[-1].raw.values())
    assert result.result.metrics is None
    assert not hasattr(result, "forecast")


@pytest.mark.parametrize("family", QUANT_FAMILIES)
def test_each_factor_is_invariant_to_per_instrument_price_units(family):
    spec = factor_spec(family)
    values = {"a": [100, 101, 105, 110, 102], "b": [500, 505, 502, 510, 508]}
    scaled = {"a": [v * 20 for v in values["a"]], "b": [v * 3 for v in values["b"]]}
    first = spec.compiled.evaluate(SimpleNamespace(field=lambda _: values))
    second = spec.compiled.evaluate(SimpleNamespace(field=lambda _: scaled))
    assert first == second


def test_zero_volatility_remains_unavailable_instead_of_zero_score():
    spec = factor_spec("risk_adjusted_momentum")
    result = spec.compiled.evaluate(SimpleNamespace(field=lambda _: {
        "constant": [100] * 8, "changing": [100, 101, 103, 106, 108, 110, 115, 118]}))
    assert result["constant"] == [None] * 8
    assert result["changing"][-1] == 0.5


def test_canonical_ties_do_not_rank_a_ticker_name():
    spec = factor_spec("cross_sectional_momentum")
    result = spec.compiled.evaluate(SimpleNamespace(field=lambda _: {
        "a": [100, 101, 102, 103], "z": [200, 202, 204, 206]}))
    assert result["a"][-1] == result["z"][-1] == 0.5


def test_composite_requires_all_active_component_values():
    spec = factor_spec("multi_factor_composite", composite_weights={
        "risk_adjusted_momentum": 0.5, "cross_sectional_momentum": 0.5})
    result = spec.compiled.evaluate(SimpleNamespace(field=lambda _: {
        "constant": [100] * 8, "changing": [100, 101, 103, 106, 108, 110, 115, 118]}))
    assert result["constant"] == [None] * 8


@pytest.mark.parametrize("overrides", [
    {"long_window": True}, {"volatility_window": 1}, {"skip_recent": -1},
    {"annual_sessions": True}, {"annual_sessions": 0},
    {"composite_weights": {"low_volatility": 1}},
])
def test_template_rejects_invalid_contract(overrides):
    with pytest.raises(ValueError):
        factor_spec("cross_sectional_momentum", **overrides)


@pytest.mark.parametrize("weights", [
    {"low_volatility": 1}, {"low_volatility": True, "short_term_reversal": 0.5},
    {"low_volatility": 0, "short_term_reversal": 0.5},
    {"low_volatility": float("inf"), "short_term_reversal": 0.5},
    {"unknown": 0.5, "short_term_reversal": 0.5},
])
def test_invalid_composite_parameters(weights):
    with pytest.raises(ValueError):
        factor_spec("multi_factor_composite", composite_weights=weights)


def test_future_columns_do_not_change_past_factor_values():
    values = {"a": [100, 101, 105, 103, 104], "b": [200, 203, 202, 201, 205]}
    for family in QUANT_FAMILIES:
        spec = factor_spec(family)
        past = spec.compiled.evaluate(SimpleNamespace(field=lambda _: values))
        extended = spec.compiled.evaluate(SimpleNamespace(field=lambda _: {
            name: [*series, 1e6] for name, series in values.items()}))
        assert all(past[name] == extended[name][:-1] for name in values)


def test_parameter_changes_have_new_identity():
    first = factor_spec("risk_adjusted_momentum")
    assert first.spec_hash != factor_spec("risk_adjusted_momentum", volatility_window=3).spec_hash
    assert first.spec_hash != factor_spec("risk_adjusted_momentum", skip_recent=1).spec_hash
    assert first.spec_hash != factor_spec("risk_adjusted_momentum", annual_sessions=250).spec_hash
    a = factor_spec("multi_factor_composite", composite_weights={"low_volatility": 0.2, "short_term_reversal": 0.8})
    b = factor_spec("multi_factor_composite", composite_weights={"short_term_reversal": 0.8, "low_volatility": 0.2})
    assert a.spec_hash == b.spec_hash


@pytest.mark.parametrize("family", QUANT_FAMILIES)
def test_actual_legacy_rebalance_scores_match_where_semantics_are_unchanged(family):
    # Import retained legacy code only in a migration golden test, never from
    # the new production implementation. AMA-167 owns retiring this dependency.
    from research_platform.backtest import PricePoint, QuantFactorConfig, QUANT_FACTOR_NAMES, run_quant_factor_backtest
    from research_platform.costs import ExecutionCostModel, SlippageTier
    days = []
    day = date(2025, 1, 2)
    while len(days) < 50:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    panel = {
        "AAA": [100 + 0.4 * i + 2 * sin(i) for i in range(50)],
        "BBB": [200 + 0.3 * i + 4 * cos(i * 0.7) for i in range(50)],
        "SPY": [100 + 0.1 * i + sin(i * 0.3) for i in range(50)],
        "SGOV": [100 + 0.01 * i for i in range(50)],
    }
    active = {family: 1.0} if family != "multi_factor_composite" else {
        "cross_sectional_momentum": 0.4, "low_volatility": 0.6}
    legacy_names = {"cross_sectional_momentum": "momentum"}
    weights = dict.fromkeys(QUANT_FACTOR_NAMES, 0.0)
    weights.update({legacy_names.get(k, k): v for k, v in active.items()})
    config = QuantFactorConfig(("AAA", "BBB"), "SGOV", tuple(weights.items()),
        6, 2, 4, 1, 1, "equal", "weekly", "none", -1, 10, 5)
    cost = ExecutionCostModel("execution-cost-model-v1", 0, 0, 1000,
        (SlippageTier(None, 0),), "synthetic", "synthetic")
    points = [PricePoint(d, name, repr(series[i]), d + "T00:00:00+00:00")
              for name, series in panel.items() for i, d in enumerate(days)]
    old = run_quant_factor_backtest(points, config, execution_cost_model=cost)
    spec = factor_spec(family, long_window=6, short_window=2, volatility_window=4, skip_recent=1)
    new = spec.compiled.evaluate(SimpleNamespace(field=lambda _: {k: panel[k] for k in ("AAA", "BBB")}))
    assert old.rebalances
    for rebalance in old.rebalances:
        index = days.index(rebalance.signal_date)
        for name, value in rebalance.scores.items():
            expected = new[name][index]
            if family != "multi_factor_composite":
                expected = 2 * expected - 1
            assert value == pytest.approx(expected)
