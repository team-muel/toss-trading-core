"""Feature definitions, versions, dependencies, and lineage."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from .models import FeatureDefinition


class FeatureRegistry:
    def __init__(self, definitions: Iterable[FeatureDefinition] = ()):
        self._definitions: dict[str, FeatureDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: FeatureDefinition) -> None:
        existing = self._definitions.get(definition.feature_id)
        if existing is not None and existing != definition:
            raise ValueError("FEATURE_DEFINITION_CONFLICT")
        self._definitions[definition.feature_id] = definition

    def get(self, feature_id: str) -> FeatureDefinition:
        try:
            return self._definitions[feature_id]
        except KeyError:
            raise ValueError("FEATURE_DEFINITION_MISSING") from None

    def catalog(self) -> tuple[dict, ...]:
        return tuple(asdict(self._definitions[key]) for key in sorted(self._definitions))


def builtin_definitions() -> tuple[FeatureDefinition, ...]:
    def d(fid: str, namespace: str, fields: tuple[str, ...], lookback: str,
          transformation: str, horizon: str = "current") -> FeatureDefinition:
        return FeatureDefinition(fid, namespace, fid.split(".")[-1], "1", fields,
                                 lookback, horizon, transformation)

    market = (
        d("market.return_1m", "market", ("adjusted_close",), "1m", "period_return"),
        d("market.return_3m", "market", ("adjusted_close",), "3m", "period_return"),
        d("market.return_6m", "market", ("adjusted_close",), "6m", "period_return"),
        d("market.return_12m", "market", ("adjusted_close",), "12m", "period_return"),
        d("market.momentum_12_1", "market", ("adjusted_close",), "12m", "momentum_12_1"),
        d("market.volatility_20d", "market", ("adjusted_close",), "21d", "realized_volatility"),
        d("market.volatility_60d", "market", ("adjusted_close",), "61d", "realized_volatility"),
        d("market.drawdown", "market", ("adjusted_close",), "expanding", "drawdown_from_high"),
        d("market.moving_average_distance", "market", ("adjusted_close",), "policy", "moving_average_distance"),
        d("market.volume_trend", "market", ("volume",), "60d", "volume_trend"),
        d("market.breadth", "market", ("adjusted_close", "historical_universe"), "policy", "market_breadth"),
        d("market.gold_slope", "market", ("gold_price",), "policy", "trend_slope"),
        d("market.credit_spread", "market", ("corporate_yield", "risk_free_yield"), "point", "credit_spread"),
    )
    company_specs = {
        "revenue_growth": ("revenue", "prior_revenue", "growth"),
        "eps_growth": ("diluted_eps", "prior_diluted_eps", "growth"),
        "fcf_growth": ("free_cash_flow", "prior_free_cash_flow", "growth"),
        "fcf_margin": ("free_cash_flow", "revenue", "fcf_margin"),
        "roic": ("nopat", "invested_capital", "roic"),
        "debt_ratio": ("debt", "assets", "debt_ratio"),
        "revenue_growth_vs_sector": ("revenue_growth", "sector_revenue_growth", "relative_growth"),
        "earnings_growth_vs_sector": ("earnings_growth", "sector_earnings_growth", "relative_growth"),
        "accrual_ratio": ("net_income", "operating_cash_flow", "average_assets", "accrual_ratio"),
        "cash_conversion": ("operating_cash_flow", "net_income", "cash_conversion"),
        "sbc_to_sales": ("stock_based_compensation", "revenue", "stock_compensation_to_sales"),
        "estimate_revision": ("current_estimate", "prior_estimate", "estimate_revision"),
        "valuation_multiple": ("valuation", "fundamental", "valuation_multiple"),
        "shareholder_yield": ("dividends", "buybacks", "share_issuance", "market_cap", "shareholder_yield"),
    }
    company = tuple(d(f"company.{name}", "company", tuple(spec[:-1]), "current-and-prior",
                      spec[-1]) for name, spec in company_specs.items())
    return market + company
