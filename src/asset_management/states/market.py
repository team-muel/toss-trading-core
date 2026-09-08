"""Continuous multi-component market state."""

from .engine import StateEngine
from .models import StateType

MARKET_COMPONENTS = ("growth", "inflation", "liquidity", "rates", "credit",
                     "volatility", "trend", "breadth", "valuation")


class MarketStateEngine(StateEngine):
    def __init__(self) -> None:
        super().__init__(state_type=StateType.MARKET, component_names=MARKET_COMPONENTS)
