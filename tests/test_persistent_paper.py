from decimal import Decimal
import pytest

from asset_management.domain.errors import DataQualityError
from asset_management.execution.paper import PersistentPaperBroker
from asset_management.execution.submission import SubmissionJournal
from test_durable_submission import args


def broker(tmp_path):
    result = PersistentPaperBroker(tmp_path / 'paper.db', account='paper:1')
    result.initialize(currency='USD', cash='1000', positions={'SPY': '5'})
    return result


def order(**changes):
    return dict(account='paper:1', client_order_id='client:1', order_intent_id='intent:1',
                instrument_id='SPY', side='BUY', quantity='2', limit_price='100', expected_fee='1') | changes


def fill(b, **changes):
    return b.record_fill(**(dict(fill_id='fill:1', client_order_id='client:1', quantity='1', price='99', fee='0.5') | changes))


def test_restart_partial_cancel_and_idempotent_fill(tmp_path):
    b = broker(tmp_path)
    submitted = b.submit_order(order())
    assert b.get_balances()['available_cash'] == '799'
    first = fill(b)
    b.close()
    b = PersistentPaperBroker(tmp_path / 'paper.db', account='paper:1')
    assert fill(b) == first
    assert b.submit_order(order())['status'] == 'PARTIALLY_FILLED'
    assert b.get_balances()['cash'] == '900.5'
    assert b.get_positions()[0]['quantity'] == '6'
    assert b.cancel(submitted['broker_order_id'], idempotency_key='client:1')['status'] == 'CANCELED'
    assert b.cancel_order('client:1')['status'] == 'CANCELED'
    assert b.get_balances()['available_cash'] == '900.5'
    assert b.get_fills() == [first]
    with pytest.raises(DataQualityError, match='NOT_OPEN'):
        fill(b, fill_id='fill:2')


def test_full_sell_reserves_positions_and_updates_cash(tmp_path):
    b = broker(tmp_path)
    b.submit_order(order(side='SELL', quantity='5'))
    assert b.get_positions()[0]['available_quantity'] == '0'
    with pytest.raises(DataQualityError, match='INSUFFICIENT_POSITION'):
        b.submit_order(order(side='SELL', client_order_id='c2', order_intent_id='i2'))
    fill(b, quantity='5', price='101', fee='1')
    assert b.get_order_status('client:1')['status'] == 'FILLED'
    assert b.get_balances()['cash'] == '1504'
    assert b.get_positions()[0]['quantity'] == '0'


@pytest.mark.parametrize('changes', [dict(quantity='11'), dict(quantity='10')])
def test_cash_includes_fees_and_outstanding_reservations(tmp_path, changes):
    b = broker(tmp_path)
    with pytest.raises(DataQualityError, match='INSUFFICIENT_CASH'):
        b.submit_order(order(**changes))
    b.submit_order(order(quantity='9'))
    with pytest.raises(DataQualityError, match='INSUFFICIENT_CASH'):
        b.submit_order(order(client_order_id='c2', order_intent_id='i2'))


@pytest.mark.parametrize('changes', [dict(quantity='3'), dict(client_order_id='c2'), dict(order_intent_id='i2')])
def test_identity_conflicts(tmp_path, changes):
    b = broker(tmp_path)
    b.submit_order(order())
    with pytest.raises(DataQualityError, match='IDEMPOTENCY_CONFLICT'):
        b.submit_order(order(**changes))


@pytest.mark.parametrize('changes', [dict(quantity='NaN'), dict(quantity=2), dict(quantity='0'),
    dict(limit_price='Infinity'), dict(side='UNKNOWN'), dict(account='live:1'), dict(expected_fee='-1'),
    dict(currency='KRW'), dict(order_type='MARKET'), dict(notional_amount='1')])
def test_invalid_orders_fail_closed(tmp_path, changes):
    b = broker(tmp_path)
    with pytest.raises(DataQualityError):
        b.submit_order(order(**changes))
    assert b.get_balances()['cash'] == '1000'


@pytest.mark.parametrize('changes', [dict(quantity='3'), dict(price='101'), dict(fee='2')])
def test_invalid_fills_do_not_mutate_state(tmp_path, changes):
    b = broker(tmp_path)
    b.submit_order(order())
    before = b.get_balances()
    with pytest.raises(DataQualityError):
        fill(b, **changes)
    assert b.get_balances() == before
    assert b.get_fills() == []


def test_initialization_explicit_and_cannot_reset_account(tmp_path):
    b = PersistentPaperBroker(tmp_path / 'paper.db', account='paper:1')
    with pytest.raises(DataQualityError, match='UNINITIALIZED'):
        b.get_balances()
    b.initialize(currency='USD', cash='1000', positions={})
    b.submit_order(order())
    fill(b)
    b.initialize(currency='USD', cash='1000', positions={})
    assert b.get_balances()['cash'] == '900.5'
    with pytest.raises(DataQualityError, match='INITIALIZATION_CONFLICT'):
        b.initialize(currency='USD', cash='2000', positions={})
    with pytest.raises(DataQualityError, match='ORDER_NOT_FOUND'):
        b.cancel_order('unknown')


def test_duplicate_fill_conflict_and_transaction_rollback(tmp_path, monkeypatch):
    b = broker(tmp_path)
    b.submit_order(order())
    fill(b)
    with pytest.raises(DataQualityError, match='FILL_CONFLICT'):
        fill(b, price='98')
    before = b.get_balances()
    original = b._save
    def crash(state):
        original(state)
        raise SystemExit('simulated crash before commit')
    monkeypatch.setattr(b, '_save', crash)
    with pytest.raises(SystemExit):
        fill(b, fill_id='fill:2')
    b.close()
    restarted = PersistentPaperBroker(tmp_path / 'paper.db', account='paper:1')
    assert restarted.get_balances() == before
    assert len(restarted.get_fills()) == 1


def test_submission_journal_transport_integration(tmp_path):
    b = broker(tmp_path)
    journal = SubmissionJournal(tmp_path / 'journal.db')
    receipt = journal.submit_once(**args(), submit=b.submit_order)
    assert receipt.state == 'UNKNOWN_BROKER_STATE'
    assert b.get_order_status(receipt.client_order_id)['status'] == 'OPEN'
    journal.submit_once(**args(), submit=b.submit_order)
    assert Decimal(b.get_balances()['reserved_cash']) == Decimal('200.03')


def test_two_connections_observe_reservation(tmp_path):
    b = broker(tmp_path)
    other = PersistentPaperBroker(tmp_path / 'paper.db', account='paper:1')
    b.submit(order(quantity='9'), idempotency_key='client:1')
    with pytest.raises(DataQualityError, match='INSUFFICIENT_CASH'):
        other.submit_order(order(client_order_id='c2', order_intent_id='i2'))
