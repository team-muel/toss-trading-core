"""Deterministic company feature transformations."""

from decimal import Decimal, InvalidOperation

from asset_management.domain.errors import DataQualityError


def _d(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataQualityError("FEATURE_INPUT_INVALID") from exc
    if not result.is_finite():
        raise DataQualityError("FEATURE_INPUT_INVALID")
    return result


def ratio(numerator: object, denominator: object) -> Decimal:
    value = _d(denominator)
    if value == 0:
        raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
    return _d(numerator) / value


def growth(current: object, prior: object) -> Decimal:
    base = abs(_d(prior))
    if base == 0:
        raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
    return (_d(current) - _d(prior)) / base


def fcf_margin(free_cash_flow: object, revenue: object) -> Decimal:
    return ratio(free_cash_flow, revenue)


def roic(nopat: object, invested_capital: object) -> Decimal:
    return ratio(nopat, invested_capital)


def debt_ratio(debt: object, assets: object) -> Decimal:
    return ratio(debt, assets)


def relative_growth(company_growth: object, sector_growth: object) -> Decimal:
    return _d(company_growth) - _d(sector_growth)


def accrual_ratio(net_income: object, operating_cash_flow: object, average_assets: object) -> Decimal:
    return ratio(_d(net_income) - _d(operating_cash_flow), average_assets)


def cash_conversion(operating_cash_flow: object, net_income: object) -> Decimal:
    return ratio(operating_cash_flow, net_income)


def stock_compensation_to_sales(stock_based_compensation: object, revenue: object) -> Decimal:
    return ratio(stock_based_compensation, revenue)


def estimate_revision(current_estimate: object, prior_estimate: object) -> Decimal:
    return growth(current_estimate, prior_estimate)


def valuation_multiple(enterprise_or_equity_value: object, fundamental: object) -> Decimal:
    return ratio(enterprise_or_equity_value, fundamental)


def shareholder_yield(dividends: object, buybacks: object, share_issuance: object,
                      market_cap: object) -> Decimal:
    return ratio(_d(dividends) + _d(buybacks) - _d(share_issuance), market_cap)
