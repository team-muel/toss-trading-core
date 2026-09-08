from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
from pathlib import Path

import pytest

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import ReconciliationError
from asset_management.ledger import (
    AccountingNavSnapshot, MoneyTranslation, PerformancePeriod, PositionMark,
    RealizedLot, account_period, account_period_with_nav,
)
from asset_management.ledger.nav_basis import NavComponent, NavComponentKind as K


NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def money(value, currency='USD', fx='1', reporting='USD'):
    return MoneyTranslation(D(value), currency, reporting, D(fx))


def component(name, kind, amount, parent=None):
    return NavComponent(name, kind, money(amount), parent, 'fixture-contract:field-inclusion@1')


def snapshot(name, components, total, at=NOW):
    return AccountingNavSnapshot('account-1', name, at, 'fixture-provider@1', tuple(components),
                                 money(total), 'nav@1')


def calculate(opening, closing, **changes):
    args = dict(opening=opening, closing=closing, positions=(), realized_lots=(), external_flows=(),
                performance_periods=(PerformancePeriod(opening.reported_nav.amount_reporting,
                                                       closing.reported_nav.amount_reporting),))
    args.update(changes)
    return account_period_with_nav(**args)


def test_settlement_receivable_becomes_cash_without_income_or_external_flow():
    opening = snapshot('open', [component('cash', K.CASH, 100),
                               component('receivable', K.SETTLEMENT_RECEIVABLE, 20)], 120)
    closing = snapshot('close', [component('cash', K.CASH, 120)], 120, NOW + timedelta(days=1))
    result = calculate(opening, closing)
    assert result['accounting']['ending_nav'] == '120'
    for key in ('total_pnl', 'net_external_flow', 'time_weighted_return'):
        assert D(result['accounting'][key]) == 0
    assert result['opening']['nav_basis']['components'][1]['kind'] == 'SETTLEMENT_RECEIVABLE'
    assert result['content_hash'] == digest(canonical({k: v for k, v in result.items() if k != 'content_hash'}))
    assert json.loads(json.dumps(result)) == result


def test_unsettled_sale_recognizes_gain_once_then_settlement_keeps_nav():
    opening = snapshot('open', [component('securities', K.SECURITIES, 100)], 100)
    unsettled = snapshot('sale', [component('cash', K.CASH, 0),
                                 component('receivable', K.SETTLEMENT_RECEIVABLE, 110)], 110,
                         NOW + timedelta(days=1))
    lot = RealizedLot(D(1), D(100), D(110), 'USD', 'USD', D(1), D(1))
    sale = calculate(opening, unsettled, realized_lots=(lot,))
    assert D(sale['accounting']['realized_pnl']) == 10
    assert D(sale['accounting']['total_pnl']) == 10
    settled = snapshot('settled', [component('cash', K.CASH, 110)], 110, NOW + timedelta(days=2))
    assert D(calculate(unsettled, settled)['accounting']['total_pnl']) == 0


def test_payable_settlement_and_included_items_do_not_double_count_or_use_buying_power():
    opening = snapshot('open', [component('cash', K.CASH, 120),
                               component('receivable', K.SETTLEMENT_RECEIVABLE, 20, 'cash'),
                               component('payable', K.SETTLEMENT_PAYABLE, -10),
                               component('power', K.BROKER_BUYING_POWER, 500)], 110)
    closing = snapshot('close', [component('cash', K.CASH, 110),
                                 component('power', K.BROKER_BUYING_POWER, 900)], 110,
                       NOW + timedelta(days=1))
    result = calculate(opening, closing)
    assert D(result['accounting']['total_pnl']) == 0
    assert D(result['accounting']['ending_nav']) == 110
    reordered = replace(opening, components=tuple(reversed(opening.components)))
    assert calculate(reordered, closing) == result


def test_deposit_remains_external_flow_under_inclusion_aware_nav():
    opening = snapshot('open', [component('cash', K.CASH, 100)], 100)
    closing = snapshot('close', [component('cash', K.CASH, 150)], 150, NOW + timedelta(days=1))
    result = calculate(opening, closing, external_flows=(money(50),), performance_periods=(
        PerformancePeriod(D(100), D(100), D(50)), PerformancePeriod(D(150), D(150))))
    assert D(result['accounting']['net_external_flow']) == 50
    assert D(result['accounting']['total_pnl']) == 0
    assert D(result['accounting']['time_weighted_return']) == 0


def test_native_position_marks_and_fx_must_match_nav_even_when_totals_match():
    opening = snapshot('open', [component('cash', K.CASH, 100)], 100)
    closing = snapshot('close', [component('securities', K.SECURITIES, 100)], 100,
                       NOW + timedelta(days=1))
    good = PositionMark('EQ', D(1), D(100), D(100), 'USD', 'USD', D(1), D(1))
    assert D(calculate(opening, closing, positions=(good,))['accounting']['total_pnl']) == 0
    bad = PositionMark('EQ', D(1), D(50), D(50), 'EUR', 'USD', D(2), D(2))
    for positions in ((), (bad,)):
        with pytest.raises(ReconciliationError, match='POSITION_MISMATCH'):
            calculate(opening, closing, positions=positions)


def test_foreign_security_pnl_reuses_native_and_fx_contribution_equations():
    opening = AccountingNavSnapshot('account-1', 'open', NOW, 'fixture-provider@1',
        (NavComponent('cash', K.CASH, money(120000, 'KRW', reporting='KRW'), None, 'fixture:cash'),),
        money(120000, 'KRW', reporting='KRW'), 'nav@1')
    closing = AccountingNavSnapshot('account-1', 'close', NOW + timedelta(days=1), 'fixture-provider@1',
        (NavComponent('securities', K.SECURITIES, money(110, 'USD', '1300', 'KRW'), None, 'fixture:marks'),),
        money(143000, 'KRW', reporting='KRW'), 'nav@1')
    position = PositionMark('EQ', D(1), D(100), D(110), 'USD', 'KRW', D(1200), D(1300))
    result = calculate(opening, closing, positions=(position,))['accounting']
    assert D(result['unrealized_pnl']) == 13000
    assert D(result['fx_contribution']) == 10000
    assert D(result['total_pnl']) == 23000


def test_missing_conflicting_source_and_period_evidence_fail_closed():
    opening = snapshot('open', [component('cash', K.CASH, 100)], 100)
    closing = snapshot('close', [component('cash', K.CASH, 100)], 100, NOW + timedelta(days=1))
    for field in ('account_id', 'snapshot_id', 'provider_contract_version'):
        with pytest.raises(ReconciliationError, match='SOURCE_REQUIRED'):
            replace(opening, **{field: ''})
    with pytest.raises(ReconciliationError, match='TIME_INVALID'):
        replace(opening, as_of=NOW.replace(tzinfo=None))
    with pytest.raises(ReconciliationError, match='SNAPSHOTS_REQUIRED'):
        account_period_with_nav(opening=None, closing=closing, positions=(), realized_lots=(),
                                external_flows=(), performance_periods=())
    with pytest.raises(ReconciliationError, match='CONTEXT_MISMATCH'):
        calculate(opening, replace(closing, account_id='another-account'))
    for bad in (replace(closing, as_of=NOW), replace(closing, snapshot_id='open')):
        with pytest.raises(ReconciliationError, match='PERIOD_INVALID'):
            calculate(opening, bad)
    with pytest.raises(ReconciliationError, match='RETURN_LINEAGE_MISMATCH'):
        calculate(opening, closing, performance_periods=(PerformancePeriod(D(99), D(100)),))


def test_unknown_pnl_and_broker_mismatch_cannot_be_balanced_with_synthetic_income():
    opening = snapshot('open', [component('cash', K.CASH, 100)], 100)
    closing = snapshot('close', [component('cash', K.CASH, 100),
                                 component('other', K.OTHER_NET_ASSETS, 1)], 101,
                       NOW + timedelta(days=1))
    with pytest.raises(ReconciliationError, match='CONTRIBUTION_MISMATCH'):
        calculate(opening, closing)
    with pytest.raises(ReconciliationError, match='BROKER_RECONCILIATION_MISMATCH'):
        replace(closing, reported_nav=money(100))


def test_v1_cash_positions_serialization_stays_unchanged_and_v2_binds_source_identity():
    legacy = account_period(reporting_currency='USD', beginning_nav=D(100), cash=(money(100),),
                            positions=(), realized_lots=(), external_flows=(),
                            performance_periods=(PerformancePeriod(D(100), D(100)),))
    # Captured from the original implementation at 17f8a18.
    assert legacy.content_hash == '1a68a72d21bcfae5c2ce86344748d92a9bcdda396badac3fdcacfa46a35e6bb4'
    opening = snapshot('open', [component('cash', K.CASH, 100)], 100)
    closing = snapshot('close', [component('cash', K.CASH, 100)], 100, NOW + timedelta(days=1))
    result = calculate(opening, closing)
    assert result['accounting'] == {key: str(value) for key, value in asdict(legacy).items()}
    changed = calculate(opening, replace(closing, provider_contract_version='fixture-provider@2'))
    assert changed['accounting'] == result['accounting']
    assert changed['content_hash'] != result['content_hash']
    schema = json.loads((Path(__file__).parents[1] / 'schemas/portfolio_accounting_result.v2.schema.json').read_text())
    assert set(schema['required']) == set(result)
