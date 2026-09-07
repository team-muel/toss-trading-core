"""Versioned per-share and aggregate equity growth identities."""
from enum import StrEnum


class EquityGrowthBasis(StrEnum):
    PER_SHARE = "PER_SHARE"
    AGGREGATE = "AGGREGATE"


EQUITY_COMPONENTS = ("dividend_yield", "per_share_fundamental_growth", "valuation_change", "tactical_overlay")
AGGREGATE_EQUITY_COMPONENTS = ("dividend_yield", "net_buyback_yield", "aggregate_fundamental_growth", "valuation_change", "tactical_overlay")
