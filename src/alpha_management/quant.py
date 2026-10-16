"""Small reviewed quant hypothesis templates, not a second factor/backtest DSL."""
from .campaign import ResearchSpec, ResearchTheme
from .dsl import MAX_TIME_WINDOW
from .expression import AlphaSimulationSettings


def momentum_spec(*, field: str, input_contract_key: str, lookback: int,
                  settings: AlphaSimulationSettings, evaluation_horizon_sessions: int,
                  policy_version: str, dataset_source: str, dataset_name: str,
                  dataset_schema_version: str) -> ResearchSpec:
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
    )
