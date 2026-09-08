from dataclasses import replace
from decimal import Decimal as D
import pytest
from asset_management.domain.errors import ReconciliationError
from asset_management.ledger.accounting import MoneyTranslation
from asset_management.ledger.nav_basis import NavComponent, NavComponentKind as K, reconcile_accounting_nav


def money(v):
    return MoneyTranslation(D(v), 'USD', 'USD', D(1))


def inputs():
    return (NavComponent('cash', K.CASH, money('120'), None, 'broker:cash-includes-receivable'),
        NavComponent('receivable', K.SETTLEMENT_RECEIVABLE, money('20'), 'cash', 'broker:included'),
        NavComponent('positions', K.SECURITIES, money('200'), None, 'broker:securities'),
        NavComponent('payable', K.SETTLEMENT_PAYABLE, money('-10'), None, 'broker:separate-payable'),
        NavComponent('power', K.BROKER_BUYING_POWER, money('500'), None, 'broker:constraint'))


def test_included_settlement_and_buying_power_are_not_counted_twice():
    result = reconcile_accounting_nav(inputs(), reported_nav=money('310'), formula_version='broker-nav@1')
    assert result['value'] == '310'
    treatments = {x['field_id']:x['treatment'] for x in result['components']}
    assert treatments['receivable'] == 'ALREADY_INCLUDED'
    assert treatments['power'] == 'EXTERNAL_CONSTRAINT_EXCLUDED'
    assert result == reconcile_accounting_nav(tuple(reversed(inputs())), reported_nav=money('310'), formula_version='broker-nav@1')


@pytest.mark.parametrize('parent', ['unknown','receivable','power'])
def test_unverifiable_inclusion_fails_closed(parent):
    values = list(inputs())
    values[1] = replace(values[1], included_in_field=parent)
    with pytest.raises(ReconciliationError, match='INCLUSION'):
        reconcile_accounting_nav(tuple(values), reported_nav=money('310'), formula_version='f@1')


def test_duplicate_mismatch_and_unknown_evidence_are_rejected():
    with pytest.raises(ReconciliationError, match='DUPLICATE'):
        reconcile_accounting_nav(inputs()+inputs()[:1], reported_nav=money('310'), formula_version='f@1')
    with pytest.raises(ReconciliationError, match='MISMATCH'):
        reconcile_accounting_nav(inputs(), reported_nav=money('330'), formula_version='f@1')
    with pytest.raises(ReconciliationError, match='UNKNOWN'):
        replace(inputs()[0], inclusion_evidence_id='')
    with pytest.raises(ReconciliationError, match='SIGN'):
        replace(inputs()[3], money=money('10'))


def test_nav_currency_is_explicit_and_fx_is_applied_once():
    c = NavComponent('cash', K.CASH, MoneyTranslation(D(100), 'USD', 'KRW', D(1300)), None, 'broker:cash')
    result = reconcile_accounting_nav((c,), reported_nav=MoneyTranslation(D(130000),'KRW','KRW',D(1)), formula_version='fx-nav@1')
    assert result['value'] == '130000'
    with pytest.raises(ReconciliationError, match='CURRENCY'):
        reconcile_accounting_nav((c,), reported_nav=money('100'), formula_version='fx-nav@1')
