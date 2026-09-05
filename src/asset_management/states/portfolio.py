"""Portfolio truth and risk state."""

from .engine import StateEngine
from .models import StateType

PORTFOLIO_COMPONENTS = ("nav", "cash_by_currency", "current_weights", "sector_exposure",
                        "factor_exposure", "currency_exposure", "volatility", "cvar",
                        "drawdown", "risk_contribution", "open_orders", "reserved_cash",
                        "unsettled_cash")


class PortfolioStateEngine(StateEngine):
    def __init__(self) -> None:
        super().__init__(state_type=StateType.PORTFOLIO, component_names=PORTFOLIO_COMPONENTS)
