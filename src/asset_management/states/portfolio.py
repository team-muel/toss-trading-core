"""Portfolio truth and risk state."""

from .engine import StateEngine
from .models import StateType

LEGACY_PORTFOLIO_COMPONENTS = ("nav", "cash_by_currency", "current_weights", "sector_exposure",
                        "factor_exposure", "currency_exposure", "volatility", "cvar",
                        "drawdown", "risk_contribution", "open_orders", "reserved_cash",
                        "unsettled_cash")
PORTFOLIO_COMPONENTS = tuple(k for k in LEGACY_PORTFOLIO_COMPONENTS if k != "risk_contribution") + (
    "variance_contribution", "volatility_contribution")


class PortfolioStateEngine(StateEngine):
    def __init__(self, *, contract_version="portfolio-state@2") -> None:
        if contract_version not in ("portfolio-state@1", "portfolio-state@2"):
            raise ValueError("PORTFOLIO_STATE_CONTRACT_UNKNOWN")
        self.contract_version = contract_version
        names = LEGACY_PORTFOLIO_COMPONENTS if contract_version == "portfolio-state@1" else PORTFOLIO_COMPONENTS
        super().__init__(state_type=StateType.PORTFOLIO, component_names=names)
