from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from asset_management.domain.errors import ReconciliationError
from asset_management.ledger import (
    DatedCashFlow, MoneyTranslation, PerformancePeriod, PositionMark, RealizedLot,
    account_period, benchmark_relative_return, money_weighted_return,
    time_weighted_return,
)


D = Decimal
UTC = timezone.utc


def money(amount, currency="KRW", fx="1"):
    return MoneyTranslation(D(amount), currency, "KRW", D(fx))


def periods():
    return (
        PerformancePeriod(D("1000"), D("1010"), D("200")),
        PerformancePeriod(D("1210"), D("1235.1")),
    )


def test_nav_contributions_currencies_and_external_flows_reconcile():
    position = PositionMark("US-EQUITY", D(1), D(100), D(110), "USD", "KRW", D("1.2"), D("1.3"))
    realized = RealizedLot(D(1), D(50), D(55), "USD", "KRW", D("1.2"), D("1.3"))
    result = account_period(
        reporting_currency="KRW", beginning_nav=D("1000"), cash=(money("1092.1"),),
        positions=(position,), realized_lots=(realized,), external_flows=(money("200"),),
        dividends=(money("2", "USD", "1.3"),), interest=(money("1"),),
        fees=(money("-2"),), taxes=(money("-1"),), performance_periods=periods(),
    )
    assert result.ending_nav == D("1235.1")
    assert result.net_external_flow == D("200")
    assert result.total_pnl == D("35.1")
    assert result.realized_pnl == D("6.5")
    assert result.unrealized_pnl == D("13.0")
    assert result.fx_contribution == D("15.0")
    assert (result.realized_pnl + result.unrealized_pnl + result.dividend_income +
            result.interest_income + result.fee_cost + result.tax_cost +
            result.fx_contribution) == result.total_pnl
    assert len(result.content_hash) == 64


def test_deposit_is_not_treated_as_investment_return():
    result = account_period(
        reporting_currency="KRW", beginning_nav=D(100), cash=(money(150),), positions=(),
        realized_lots=(), external_flows=(money(50),),
        performance_periods=(PerformancePeriod(D(100), D(100), D(50)),
                             PerformancePeriod(D(150), D(150))),
    )
    assert result.total_pnl == 0
    assert result.time_weighted_return == 0


def test_time_weighted_return_requires_valuation_at_each_flow_boundary():
    expected = D("1.01") * (D("1235.1") / D("1210")) - 1
    assert time_weighted_return(periods()) == expected
    with pytest.raises(ReconciliationError, match="FLOW_VALUATION_MISMATCH"):
        time_weighted_return((PerformancePeriod(D(100), D(101), D(50)),
                              PerformancePeriod(D(150), D(151))))
    with pytest.raises(ReconciliationError, match="TERMINAL_FLOW_UNVALUED"):
        time_weighted_return((PerformancePeriod(D(100), D(101), D(1)),))


def test_money_weighted_return_and_benchmark_relative_interface():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    irr = money_weighted_return((DatedCashFlow(start, D(-100)),
                                 DatedCashFlow(start + timedelta(days=365), D(110))))
    assert abs(irr - D("0.1")) < D("0.000000001")
    assert benchmark_relative_return(D("0.10"), D("0.05")) == D("1.1") / D("1.05") - 1


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), 1.5])
def test_accounting_rejects_inexact_or_unknown_values(bad):
    with pytest.raises(ReconciliationError, match="PORTFOLIO_ACCOUNTING_INVALID"):
        MoneyTranslation(bad, "KRW", "KRW", D(1))


def test_currency_and_component_mismatches_fail_closed():
    with pytest.raises(ReconciliationError, match="REPORTING_CURRENCY_CONFLICT"):
        account_period(reporting_currency="KRW", beginning_nav=D(100),
                       cash=(MoneyTranslation(D(100), "USD", "USD", D(1)),), positions=(),
                       realized_lots=(), external_flows=(),
                       performance_periods=(PerformancePeriod(D(100), D(100)),))
    with pytest.raises(ReconciliationError, match="CONTRIBUTION_MISMATCH"):
        account_period(reporting_currency="KRW", beginning_nav=D(100), cash=(money(101),),
                       positions=(), realized_lots=(), external_flows=(),
                       performance_periods=(PerformancePeriod(D(100), D(101)),))
    with pytest.raises(ReconciliationError, match="COST_SIGN_INVALID"):
        account_period(reporting_currency="KRW", beginning_nav=D(100), cash=(money(100),),
                       positions=(), realized_lots=(), external_flows=(), fees=(money(1),),
                       performance_periods=(PerformancePeriod(D(100), D(100)),))


def test_performance_periods_are_bound_to_nav_and_external_flow_ledger():
    with pytest.raises(ReconciliationError, match="RETURN_LINEAGE_MISMATCH"):
        account_period(reporting_currency="KRW", beginning_nav=D(100), cash=(money(150),),
                       positions=(), realized_lots=(), external_flows=(money(50),),
                       performance_periods=(PerformancePeriod(D(100), D(100)),))
    with pytest.raises(ReconciliationError, match="RETURN_LINEAGE_MISMATCH"):
        account_period(reporting_currency="KRW", beginning_nav=D(100), cash=(money(100),),
                       positions=(), realized_lots=(), external_flows=(),
                       performance_periods=(PerformancePeriod(D(99), D(100)),))


def test_irr_requires_aware_times_sign_change_and_bracket():
    with pytest.raises(ReconciliationError, match="CASH_FLOW_TIME_INVALID"):
        DatedCashFlow(datetime(2025, 1, 1), D(-100))
    start = datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(ReconciliationError, match="IRR_INPUT_INVALID"):
        money_weighted_return((DatedCashFlow(start, D(100)),
                               DatedCashFlow(start + timedelta(days=1), D(1))))
    with pytest.raises(ReconciliationError, match="IRR_NOT_BRACKETED"):
        money_weighted_return((DatedCashFlow(start, D(-100)),
                               DatedCashFlow(start + timedelta(days=365), D(2000))), upper=D(1))


def test_result_schema_contract_is_complete_and_serializable():
    schema = json.loads((__import__("pathlib").Path(__file__).parents[1] /
                         "schemas/portfolio_accounting_result.schema.json").read_text())
    required = set(schema["required"])
    assert required == set(asdict(account_period(
        reporting_currency="KRW", beginning_nav=D(100), cash=(money(100),), positions=(),
        realized_lots=(), external_flows=(),
        performance_periods=(PerformancePeriod(D(100), D(100)),),
    )))
