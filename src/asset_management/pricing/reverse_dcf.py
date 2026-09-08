"""Reverse DCF solvers that expose market-implied assumptions."""
from __future__ import annotations
from dataclasses import dataclass, replace
from decimal import Decimal
from asset_management.domain.errors import DataQualityError

SOLVABLE_FIELDS = frozenset({"revenue_growth", "operating_margin", "fcf_margin", "discount_rate", "terminal_growth"})

@dataclass(frozen=True)
class DcfAssumptions:
    revenue: Decimal
    shares: Decimal
    years: int = 5
    revenue_growth: Decimal = Decimal("0.05")
    operating_margin: Decimal = Decimal("0.20")
    fcf_margin: Decimal = Decimal("0.10")
    tax_rate: Decimal = Decimal("0.20")
    reinvestment_rate: Decimal = Decimal("0.375")
    discount_rate: Decimal = Decimal("0.09")
    terminal_growth: Decimal = Decimal("0.02")
    net_debt: Decimal = Decimal(0)

@dataclass(frozen=True)
class ReverseDcfResult:
    field: str
    implied_value: Decimal
    model_price: Decimal
    iterations: int
    uncertainty: Decimal

def dcf_price(x: DcfAssumptions) -> Decimal:
    values = (x.revenue, x.shares, x.revenue_growth, x.operating_margin, x.fcf_margin,
              x.tax_rate, x.reinvestment_rate, x.discount_rate, x.terminal_growth, x.net_debt)
    if (any(not v.is_finite() for v in values) or x.revenue <= 0 or x.shares <= 0 or x.years < 1 or
            x.discount_rate <= x.terminal_growth or x.discount_rate <= -1 or x.revenue_growth <= -1 or
            x.fcf_margin < 0 or x.operating_margin < 0 or not Decimal(0)<=x.tax_rate<Decimal(1) or
            not Decimal(0)<=x.reinvestment_rate<=Decimal(1) or
            x.fcf_margin != x.operating_margin*(1-x.tax_rate)*(1-x.reinvestment_rate)):
        raise DataQualityError("REVERSE_DCF_INPUT_INVALID")
    revenue = x.revenue; enterprise = Decimal(0)
    for year in range(1, x.years + 1):
        revenue *= 1 + x.revenue_growth
        enterprise += revenue * x.fcf_margin / (1 + x.discount_rate) ** year
    terminal = (revenue * (1 + x.terminal_growth) * x.fcf_margin /
                (x.discount_rate - x.terminal_growth))
    return (enterprise + terminal / (1 + x.discount_rate) ** x.years - x.net_debt) / x.shares

def solve_implied(*, market_price: Decimal, assumptions: DcfAssumptions, field: str,
                  lower: Decimal, upper: Decimal, tolerance: Decimal = Decimal("0.000001"),
                  max_iterations: int = 200) -> ReverseDcfResult:
    if (field not in SOLVABLE_FIELDS or not market_price.is_finite() or market_price <= 0 or
            lower >= upper or tolerance <= 0 or max_iterations < 1):
        raise DataQualityError("REVERSE_DCF_SOLVER_INPUT_INVALID")
    def scenario(v: Decimal) -> DcfAssumptions:
        if field == "operating_margin":
            return replace(assumptions, operating_margin=v,
                           fcf_margin=v*(1-assumptions.tax_rate)*(1-assumptions.reinvestment_rate))
        if field == "fcf_margin":
            denominator=(1-assumptions.tax_rate)*(1-assumptions.reinvestment_rate)
            if denominator<=0: raise DataQualityError("REVERSE_DCF_INPUT_INVALID")
            return replace(assumptions, fcf_margin=v, operating_margin=v/denominator)
        return replace(assumptions, **{field: v})
    def objective(v: Decimal) -> Decimal: return dcf_price(scenario(v))-market_price
    low_value, high_value = objective(lower), objective(upper)
    if low_value * high_value > 0: raise DataQualityError("REVERSE_DCF_ROOT_NOT_BRACKETED")
    for iteration in range(max_iterations + 1):
        midpoint = (lower + upper) / 2; mid_value = objective(midpoint)
        if abs(mid_value) <= tolerance or upper - lower <= tolerance:
            price = dcf_price(scenario(midpoint))
            return ReverseDcfResult(field, midpoint, price, iteration, upper - lower)
        if low_value * mid_value <= 0: upper = midpoint
        else: lower, low_value = midpoint, mid_value
    raise DataQualityError("REVERSE_DCF_DID_NOT_CONVERGE")
