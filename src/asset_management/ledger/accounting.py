"""Exact portfolio accounting with explicit currency translation and flow separation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from typing import Iterable

from asset_management.domain.decimal import exact_decimal
from asset_management.domain.errors import InvariantViolation, ReconciliationError


def _decimal(value: Decimal, name: str) -> Decimal:
    try:
        result = exact_decimal(value)
    except (TypeError, ValueError, InvariantViolation) as exc:
        raise ReconciliationError(f"PORTFOLIO_ACCOUNTING_INVALID: {name}") from exc
    if not result.is_finite():
        raise ReconciliationError(f"PORTFOLIO_ACCOUNTING_INVALID: {name}")
    return result


def _currency(value: str, name: str) -> str:
    result = value.strip().upper()
    if len(result) != 3 or not result.isalpha():
        raise ReconciliationError(f"PORTFOLIO_ACCOUNTING_INVALID: {name}")
    return result


@dataclass(frozen=True, slots=True)
class MoneyTranslation:
    amount_native: Decimal
    native_currency: str
    reporting_currency: str
    fx_to_reporting: Decimal

    def __post_init__(self) -> None:
        amount = _decimal(self.amount_native, "amount_native")
        rate = _decimal(self.fx_to_reporting, "fx_to_reporting")
        native = _currency(self.native_currency, "native_currency")
        reporting = _currency(self.reporting_currency, "reporting_currency")
        if rate <= 0 or (native == reporting and rate != 1):
            raise ReconciliationError("PORTFOLIO_ACCOUNTING_FX_INVALID")
        object.__setattr__(self, "amount_native", amount)
        object.__setattr__(self, "fx_to_reporting", rate)
        object.__setattr__(self, "native_currency", native)
        object.__setattr__(self, "reporting_currency", reporting)

    @property
    def amount_reporting(self) -> Decimal:
        return self.amount_native * self.fx_to_reporting


@dataclass(frozen=True, slots=True)
class PositionMark:
    instrument_id: str
    quantity: Decimal
    unit_cost_native: Decimal
    market_price_native: Decimal
    native_currency: str
    reporting_currency: str
    acquisition_fx: Decimal
    current_fx: Decimal

    def __post_init__(self) -> None:
        values = tuple(_decimal(value, name) for value, name in (
            (self.quantity, "quantity"), (self.unit_cost_native, "unit_cost_native"),
            (self.market_price_native, "market_price_native"),
            (self.acquisition_fx, "acquisition_fx"), (self.current_fx, "current_fx"),
        ))
        if (not self.instrument_id.strip() or values[0] < 0 or values[1] < 0 or
                values[2] < 0 or values[3] <= 0 or values[4] <= 0):
            raise ReconciliationError("PORTFOLIO_ACCOUNTING_POSITION_INVALID")
        native = _currency(self.native_currency, "native_currency")
        reporting = _currency(self.reporting_currency, "reporting_currency")
        if native == reporting and (values[3] != 1 or values[4] != 1):
            raise ReconciliationError("PORTFOLIO_ACCOUNTING_FX_INVALID")
        for name, value in zip(("quantity", "unit_cost_native", "market_price_native",
                                "acquisition_fx", "current_fx"), values):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "native_currency", native)
        object.__setattr__(self, "reporting_currency", reporting)

    @property
    def market_value_reporting(self) -> Decimal:
        return self.quantity * self.market_price_native * self.current_fx

    @property
    def local_unrealized_pnl(self) -> Decimal:
        return self.quantity * (self.market_price_native - self.unit_cost_native) * self.current_fx

    @property
    def fx_contribution(self) -> Decimal:
        return self.quantity * self.unit_cost_native * (self.current_fx - self.acquisition_fx)


@dataclass(frozen=True, slots=True)
class RealizedLot:
    quantity: Decimal
    unit_cost_native: Decimal
    sale_price_native: Decimal
    native_currency: str
    reporting_currency: str
    acquisition_fx: Decimal
    sale_fx: Decimal

    def __post_init__(self) -> None:
        values = tuple(_decimal(value, name) for value, name in (
            (self.quantity, "quantity"), (self.unit_cost_native, "unit_cost_native"),
            (self.sale_price_native, "sale_price_native"),
            (self.acquisition_fx, "acquisition_fx"), (self.sale_fx, "sale_fx"),
        ))
        if values[0] <= 0 or values[1] < 0 or values[2] < 0 or values[3] <= 0 or values[4] <= 0:
            raise ReconciliationError("PORTFOLIO_ACCOUNTING_REALIZED_LOT_INVALID")
        native = _currency(self.native_currency, "native_currency")
        reporting = _currency(self.reporting_currency, "reporting_currency")
        if native == reporting and (values[3] != 1 or values[4] != 1):
            raise ReconciliationError("PORTFOLIO_ACCOUNTING_FX_INVALID")
        for name, value in zip(("quantity", "unit_cost_native", "sale_price_native",
                                "acquisition_fx", "sale_fx"), values):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "native_currency", native)
        object.__setattr__(self, "reporting_currency", reporting)

    @property
    def local_realized_pnl(self) -> Decimal:
        return self.quantity * (self.sale_price_native - self.unit_cost_native) * self.sale_fx

    @property
    def fx_contribution(self) -> Decimal:
        return self.quantity * self.unit_cost_native * (self.sale_fx - self.acquisition_fx)


@dataclass(frozen=True, slots=True)
class PerformancePeriod:
    opening_nav: Decimal
    closing_nav_before_flow: Decimal
    external_flow_after_close: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        opening = _decimal(self.opening_nav, "opening_nav")
        closing = _decimal(self.closing_nav_before_flow, "closing_nav_before_flow")
        flow = _decimal(self.external_flow_after_close, "external_flow_after_close")
        if opening <= 0 or closing < 0:
            raise ReconciliationError("PORTFOLIO_ACCOUNTING_RETURN_PERIOD_INVALID")
        object.__setattr__(self, "opening_nav", opening)
        object.__setattr__(self, "closing_nav_before_flow", closing)
        object.__setattr__(self, "external_flow_after_close", flow)


@dataclass(frozen=True, slots=True)
class DatedCashFlow:
    occurred_at: datetime
    amount_reporting: Decimal

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ReconciliationError("PORTFOLIO_ACCOUNTING_CASH_FLOW_TIME_INVALID")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(timezone.utc))
        object.__setattr__(self, "amount_reporting", _decimal(self.amount_reporting, "amount_reporting"))


@dataclass(frozen=True, slots=True)
class AccountingResult:
    reporting_currency: str
    ending_nav: Decimal
    net_external_flow: Decimal
    total_pnl: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    dividend_income: Decimal
    interest_income: Decimal
    fee_cost: Decimal
    tax_cost: Decimal
    fx_contribution: Decimal
    time_weighted_return: Decimal
    content_hash: str


def time_weighted_return(periods: Iterable[PerformancePeriod]) -> Decimal:
    periods = tuple(periods)
    if not periods:
        raise ReconciliationError("PORTFOLIO_ACCOUNTING_RETURN_PERIOD_MISSING")
    compound = Decimal(1)
    for index, period in enumerate(periods):
        compound *= period.closing_nav_before_flow / period.opening_nav
        if index + 1 < len(periods):
            expected = period.closing_nav_before_flow + period.external_flow_after_close
            if periods[index + 1].opening_nav != expected:
                raise ReconciliationError("PORTFOLIO_ACCOUNTING_FLOW_VALUATION_MISMATCH")
        elif period.external_flow_after_close != 0:
            raise ReconciliationError("PORTFOLIO_ACCOUNTING_TERMINAL_FLOW_UNVALUED")
    return compound - 1


def money_weighted_return(cash_flows: Iterable[DatedCashFlow], *,
                          lower: Decimal = Decimal("-0.9999"),
                          upper: Decimal = Decimal("10"),
                          tolerance: Decimal = Decimal("0.000000000001"),
                          max_iterations: int = 300) -> Decimal:
    """XIRR helper. Investor contributions are negative; withdrawals and terminal NAV positive."""
    flows = tuple(sorted(cash_flows, key=lambda item: item.occurred_at))
    if (len(flows) < 2 or not any(item.amount_reporting < 0 for item in flows) or
            not any(item.amount_reporting > 0 for item in flows) or lower <= -1 or
            lower >= upper or tolerance <= 0 or max_iterations < 1):
        raise ReconciliationError("PORTFOLIO_ACCOUNTING_IRR_INPUT_INVALID")
    start = flows[0].occurred_at

    def npv(rate: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = 34
            total = Decimal(0)
            for item in flows:
                years = Decimal((item.occurred_at - start).total_seconds()) / Decimal("31536000")
                discount = ((Decimal(1) + rate).ln() * years).exp()
                total += item.amount_reporting / discount
            return total

    low_value, high_value = npv(lower), npv(upper)
    if low_value == 0:
        return lower
    if high_value == 0:
        return upper
    if low_value * high_value > 0:
        raise ReconciliationError("PORTFOLIO_ACCOUNTING_IRR_NOT_BRACKETED")
    for _ in range(max_iterations):
        midpoint = (lower + upper) / 2
        value = npv(midpoint)
        if abs(value) <= tolerance or upper - lower <= tolerance:
            return midpoint
        if low_value * value <= 0:
            upper = midpoint
        else:
            lower, low_value = midpoint, value
    raise ReconciliationError("PORTFOLIO_ACCOUNTING_IRR_DID_NOT_CONVERGE")


def benchmark_relative_return(portfolio_return: Decimal, benchmark_return: Decimal) -> Decimal:
    portfolio = _decimal(portfolio_return, "portfolio_return")
    benchmark = _decimal(benchmark_return, "benchmark_return")
    if portfolio <= -1 or benchmark <= -1:
        raise ReconciliationError("PORTFOLIO_ACCOUNTING_RETURN_INVALID")
    return (Decimal(1) + portfolio) / (Decimal(1) + benchmark) - 1


def account_period(*, reporting_currency: str, beginning_nav: Decimal,
                   cash: Iterable[MoneyTranslation], positions: Iterable[PositionMark],
                   realized_lots: Iterable[RealizedLot], external_flows: Iterable[MoneyTranslation],
                   dividends: Iterable[MoneyTranslation] = (), interest: Iterable[MoneyTranslation] = (),
                   fees: Iterable[MoneyTranslation] = (), taxes: Iterable[MoneyTranslation] = (),
                   performance_periods: Iterable[PerformancePeriod]) -> AccountingResult:
    reporting = _currency(reporting_currency, "reporting_currency")
    beginning = _decimal(beginning_nav, "beginning_nav")
    if beginning < 0:
        raise ReconciliationError("PORTFOLIO_ACCOUNTING_BEGINNING_NAV_INVALID")
    groups = tuple(tuple(group) for group in
                   (cash, positions, realized_lots, external_flows, dividends, interest, fees, taxes))
    for group in groups:
        if any(item.reporting_currency != reporting for item in group):
            raise ReconciliationError("PORTFOLIO_ACCOUNTING_REPORTING_CURRENCY_CONFLICT")
    cash_items, position_items, realized_items, flow_items, dividend_items, interest_items, fee_items, tax_items = groups
    period_items = tuple(performance_periods)
    if any(item.amount_native > 0 for item in fee_items + tax_items):
        raise ReconciliationError("PORTFOLIO_ACCOUNTING_COST_SIGN_INVALID")

    ending_nav = sum((item.amount_reporting for item in cash_items), Decimal(0)) + sum(
        (item.market_value_reporting for item in position_items), Decimal(0))
    net_external_flow = sum((item.amount_reporting for item in flow_items), Decimal(0))
    total_pnl = ending_nav - beginning - net_external_flow
    realized = sum((item.local_realized_pnl for item in realized_items), Decimal(0))
    unrealized = sum((item.local_unrealized_pnl for item in position_items), Decimal(0))
    dividend = sum((item.amount_reporting for item in dividend_items), Decimal(0))
    interest_income = sum((item.amount_reporting for item in interest_items), Decimal(0))
    fee_cost = sum((item.amount_reporting for item in fee_items), Decimal(0))
    tax_cost = sum((item.amount_reporting for item in tax_items), Decimal(0))
    fx = sum((item.fx_contribution for item in position_items + realized_items), Decimal(0))
    components = realized + unrealized + dividend + interest_income + fee_cost + tax_cost + fx
    if components != total_pnl:
        raise ReconciliationError("PORTFOLIO_ACCOUNTING_CONTRIBUTION_MISMATCH")
    if (not period_items or period_items[0].opening_nav != beginning or
            period_items[-1].closing_nav_before_flow != ending_nav or
            sum((item.external_flow_after_close for item in period_items), Decimal(0)) != net_external_flow):
        raise ReconciliationError("PORTFOLIO_ACCOUNTING_RETURN_LINEAGE_MISMATCH")
    twr = time_weighted_return(period_items)
    payload = {
        "reporting_currency": reporting, "ending_nav": str(ending_nav),
        "net_external_flow": str(net_external_flow), "total_pnl": str(total_pnl),
        "realized_pnl": str(realized), "unrealized_pnl": str(unrealized),
        "dividend_income": str(dividend), "interest_income": str(interest_income),
        "fee_cost": str(fee_cost), "tax_cost": str(tax_cost),
        "fx_contribution": str(fx), "time_weighted_return": str(twr),
    }
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return AccountingResult(reporting, ending_nav, net_external_flow, total_pnl, realized,
                            unrealized, dividend, interest_income, fee_cost, tax_cost, fx, twr, digest)
