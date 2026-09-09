from collections.abc import Mapping
from dataclasses import replace
from math import isfinite, sqrt
"""Small reviewed quant hypothesis templates, not a second factor/backtest DSL."""
from .campaign import ResearchSpec, ResearchTheme
from .dsl import MAX_TIME_WINDOW
from .expression import AlphaSimulationSettings


def momentum_spec(*, field: str, input_contract_key: str, lookback: int,
                  settings: AlphaSimulationSettings, evaluation_horizon_sessions: int,
                  policy_version: str, dataset_source: str, dataset_name: str,
                  dataset_schema_version: str, neutralization_group_field: str | None = None) -> ResearchSpec:
    """Rank simple returns over an upstream governed total-return index.

    The input contract key must identify the real adjustment/currency/calendar
    contract supplied by the data owner. Naming a field cannot establish it.
    """
    if type(lookback) is not int or not 1 <= lookback <= MAX_TIME_WINDOW:
        raise ValueError("lookback must be a bounded positive integer")
    return ResearchSpec(
        theme=ResearchTheme.QUANT, family="cross_sectional_momentum", version="1",
        thesis="Relative strength in a governed total-return index may persist over the declared evaluation horizon.",
        falsification_criteria=(
            "No stable incremental out-of-sample predictive value at the preregistered horizon.",
            "Predictive value disappears after turnover and transaction-cost sensitivity assessment.",
        ),
        expression=f"rank(ts_return({field},{lookback}))",
        field_contracts={field: input_contract_key}, settings=settings,
        evaluation_horizon_sessions=evaluation_horizon_sessions, policy_version=policy_version,
        dataset_source=dataset_source, dataset_name=dataset_name,
        dataset_schema_version=dataset_schema_version,
        neutralization_group_field=neutralization_group_field,
    )


QUANT_FAMILIES = (
    "cross_sectional_momentum", "risk_adjusted_momentum", "short_term_reversal",
    "low_volatility", "trend_acceleration", "multi_factor_composite",
)


def _window(value: int, name: str, *, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= MAX_TIME_WINDOW:
        raise ValueError(f"{name} must be a bounded integer >= {minimum}")
    return value


def quant_factor_spec(*, family: str, field: str, input_contract_key: str,
                      settings: AlphaSimulationSettings, evaluation_horizon_sessions: int,
                      policy_version: str, dataset_source: str, dataset_name: str,
                      dataset_schema_version: str, long_window: int = 126,
                      short_window: int = 21, volatility_window: int = 63,
                      skip_recent: int = 0, annual_sessions: int = 252,
                      composite_weights: Mapping[str, float] | None = None,
                      neutralization_group_field: str | None = None) -> ResearchSpec:
    """Compile a bounded economic hypothesis into the existing expression API.

    Individual outputs are canonical average-tie ranks in [0, 1]. A composite
    is a separately identified research hypothesis over centered ranks [-1, 1],
    not a production forecast combination or an assertion of independent alpha.
    This factory owns neither data acquisition nor portfolio construction.
    """
    if family not in QUANT_FAMILIES:
        raise ValueError("unknown quant family")
    _window(long_window, "long_window")
    _window(short_window, "short_window")
    _window(volatility_window, "volatility_window", minimum=2)
    _window(skip_recent, "skip_recent", minimum=0)
    if type(annual_sessions) is not int or not 1 <= annual_sessions <= 366:
        raise ValueError("annual_sessions must be an explicit bounded session count")
    if family in {"trend_acceleration", "multi_factor_composite"} and long_window <= short_window:
        raise ValueError("long_window must exceed short_window")
    base = momentum_spec(field=field, input_contract_key=input_contract_key,
        lookback=long_window, settings=settings,
        evaluation_horizon_sessions=evaluation_horizon_sessions, policy_version=policy_version,
        dataset_source=dataset_source, dataset_name=dataset_name,
        dataset_schema_version=dataset_schema_version,
        neutralization_group_field=neutralization_group_field)
    source = field if skip_recent == 0 else f"ts_delay({field},{skip_recent})"
    long_return = f"ts_return({source},{long_window})"
    short_return = f"ts_return({source},{short_window})"
    annual_scale = repr(sqrt(annual_sessions))
    volatility = f"multiply(ts_stddev(ts_return({source},1),{volatility_window}),{annual_scale})"
    raw = {
        "cross_sectional_momentum": long_return,
        "risk_adjusted_momentum": f"divide({long_return},{volatility})",
        "short_term_reversal": f"negate({short_return})",
        "low_volatility": f"negate({volatility})",
        "trend_acceleration": f"subtract(divide({short_return},{short_window}),divide({long_return},{long_window}))",
    }
    theses = {
        "cross_sectional_momentum": "Relative total-return strength may persist.",
        "risk_adjusted_momentum": "Total-return strength per unit of historical volatility may persist.",
        "short_term_reversal": "Short-lived price pressure may reverse rather than represent permanent information.",
        "low_volatility": "Lower historical volatility may contain incremental predictive information, distinct from a risk-budget decision.",
        "trend_acceleration": "An improving recent return-per-session trend may contain predictive information beyond the long-window trend.",
        "multi_factor_composite": "A preregistered combination of independently examined factor ranks may contain incremental information.",
    }
    if family != "multi_factor_composite":
        if composite_weights is not None:
            raise ValueError("weights are only valid for a composite hypothesis")
        expression = f"rank({raw[family]})"
    else:
        if not isinstance(composite_weights, Mapping) or not 2 <= len(composite_weights) <= 3:
            raise ValueError("a composite requires two or three active components")
        terms, total = [], 0.0
        for name, value in sorted(composite_weights.items()):
            if name not in raw or type(value) not in (float, int) or not isfinite(value) or not 0 < value <= 1:
                raise ValueError("invalid composite component or weight")
            weight = float(value)
            terms.append(f"multiply(subtract(multiply(rank({raw[name]}),2),1),{weight!r})")
            total += weight
        combined = terms[0]
        for term in terms[1:]:
            combined = f"add({combined},{term})"
        expression = f"divide({combined},{total!r})"
    return replace(base, family=family, version="canonical-factors-v1",
        thesis=theses[family], expression=expression,
        falsification_criteria=(
            "No stable incremental chronological out-of-sample predictive value at the preregistered horizon.",
            "Predictive value disappears under turnover, cost, regime or parameter-stability analysis.",
            "The apparent result depends on missing data, arbitrary tie-breaking or an undefined denominator.",
        ))
