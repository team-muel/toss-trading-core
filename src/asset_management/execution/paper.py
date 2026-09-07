"""Persistent, single-currency paper broker. Never calls a live transport."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from asset_management.domain.errors import DataQualityError


def _number(value, *, positive=False):
    if not isinstance(value, (str, Decimal)):
        raise DataQualityError('PAPER_INVALID_DECIMAL')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise DataQualityError('PAPER_INVALID_DECIMAL') from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise DataQualityError('PAPER_INVALID_DECIMAL')
    return result


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DataQualityError('PAPER_INVALID_PAYLOAD') from exc


class PersistentPaperBroker:
    """Explicit initialization; limit orders, long-only, caller-supplied fills."""

    def __init__(self, path, *, account):
        if not isinstance(account, str) or not account.startswith('paper:') or len(account) <= 6:
            raise DataQualityError('PAPER_ACCOUNT_REQUIRED')
        self.account = account
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.execute('PRAGMA synchronous=FULL')
        self.conn.execute('CREATE TABLE IF NOT EXISTS paper_state '
                          '(account TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def close(self):
        self.conn.close()

    @contextmanager
    def _transaction(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def _read(self):
        row = self.conn.execute('SELECT body FROM paper_state WHERE account=?',
                                (self.account,)).fetchone()
        if row is None:
            raise DataQualityError('PAPER_ACCOUNT_UNINITIALIZED')
        return json.loads(row[0])

    def _save(self, state):
        self.conn.execute('UPDATE paper_state SET body=? WHERE account=?',
                          (_json(state), self.account))

    def initialize(self, *, currency, cash, positions):
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper():
            raise DataQualityError('PAPER_CURRENCY_REQUIRED')
        if not isinstance(positions, dict) or any(not isinstance(k, str) or not k.strip() for k in positions):
            raise DataQualityError('PAPER_INVALID_POSITIONS')
        initial = dict(currency=currency, cash=str(_number(cash)),
                       positions={k: str(_number(v)) for k, v in positions.items()})
        with self._transaction():
            row = self.conn.execute('SELECT body FROM paper_state WHERE account=?', (self.account,)).fetchone()
            if row:
                if json.loads(row[0])['initial'] != initial:
                    raise DataQualityError('PAPER_INITIALIZATION_CONFLICT')
                return
            state = dict(initial=initial, **initial, orders={}, fills={})
            self.conn.execute('INSERT INTO paper_state VALUES (?,?)', (self.account, _json(state)))

    @staticmethod
    def _reserved(state):
        cash = Decimal(0)
        positions = {}
        for order in state['orders'].values():
            if order['status'] not in ('OPEN', 'PARTIALLY_FILLED'):
                continue
            p = order['request']
            remaining = Decimal(p['quantity']) - Decimal(order['filled_quantity'])
            if p['side'] == 'BUY':
                cash += remaining * Decimal(p['limit_price']) + Decimal(order['remaining_fee'])
            else:
                instrument = p['instrument_id']
                positions[instrument] = positions.get(instrument, Decimal(0)) + remaining
                cash += Decimal(order['remaining_fee'])
        return cash, positions

    def submit_order(self, order):
        request = json.loads(_json(dict(order)))
        for key in ('client_order_id', 'order_intent_id', 'instrument_id'):
            if not isinstance(request.get(key), str) or not request[key].strip():
                raise DataQualityError('PAPER_ORDER_IDENTITY_REQUIRED')
        if request.get('account') != self.account or request.get('side') not in ('BUY', 'SELL'):
            raise DataQualityError('PAPER_ORDER_OWNERSHIP_OR_SIDE')
        quantity = _number(request.get('quantity'), positive=True)
        price = _number(request.get('limit_price'), positive=True)
        fee = _number(request.get('expected_fee'))
        if request.get('order_type', 'LIMIT') != 'LIMIT':
            raise DataQualityError('PAPER_UNSUPPORTED_ORDER_TYPE')
        if 'notional_amount' in request and _number(request['notional_amount']) != quantity * price:
            raise DataQualityError('PAPER_NOTIONAL_CONFLICT')
        client = request['client_order_id']
        with self._transaction():
            state = self._read()
            if request.get('currency', state['currency']) != state['currency']:
                raise DataQualityError('PAPER_CURRENCY_CONFLICT')
            for existing in state['orders'].values():
                if existing['client_order_id'] == client or existing['request']['order_intent_id'] == request['order_intent_id']:
                    if existing['request'] != request:
                        raise DataQualityError('PAPER_IDEMPOTENCY_CONFLICT')
                    return existing
            reserved_cash, reserved_positions = self._reserved(state)
            needed = quantity * price + fee if request['side'] == 'BUY' else fee
            if Decimal(state['cash']) - reserved_cash < needed:
                raise DataQualityError('PAPER_INSUFFICIENT_CASH')
            instrument = request['instrument_id']
            if request['side'] == 'SELL' and Decimal(state['positions'].get(instrument, '0')) - reserved_positions.get(instrument, Decimal(0)) < quantity:
                raise DataQualityError('PAPER_INSUFFICIENT_POSITION')
            result = dict(client_order_id=client, broker_order_id='paper-' + hashlib.sha256(
                _json([self.account, client]).encode()).hexdigest(), status='OPEN',
                filled_quantity='0', remaining_fee=str(fee), request=request)
            state['orders'][client] = result
            self._save(state)
            return result

    def submit(self, request, *, idempotency_key):
        if request.get('client_order_id') != idempotency_key:
            raise DataQualityError('PAPER_IDEMPOTENCY_KEY_MISMATCH')
        return self.submit_order(request)

    def get_order_status(self, client_order_id):
        result = self._read()['orders'].get(client_order_id)
        if result is None:
            raise DataQualityError('PAPER_ORDER_NOT_FOUND')
        return result

    def cancel_order(self, client_order_id):
        with self._transaction():
            state = self._read()
            order = state['orders'].get(client_order_id)
            if order is None:
                raise DataQualityError('PAPER_ORDER_NOT_FOUND')
            if order['status'] in ('OPEN', 'PARTIALLY_FILLED'):
                order['status'] = 'CANCELED'
                self._save(state)
            return order

    def cancel(self, broker_order_id, *, idempotency_key):
        # Cancellation has one fixed effect; key is bound to the client identity.
        order = self.get_order_status(idempotency_key)
        if order['broker_order_id'] != broker_order_id:
            raise DataQualityError('PAPER_CANCEL_IDENTITY_CONFLICT')
        return self.cancel_order(idempotency_key)

    def record_fill(self, *, fill_id, client_order_id, quantity, price, fee):
        if not isinstance(fill_id, str) or not fill_id.strip():
            raise DataQualityError('PAPER_FILL_ID_REQUIRED')
        quantity, price, fee = _number(quantity, positive=True), _number(price, positive=True), _number(fee)
        fill = dict(fill_id=fill_id, client_order_id=client_order_id,
                    quantity=str(quantity), price=str(price), fee=str(fee))
        with self._transaction():
            state = self._read()
            if fill_id in state['fills']:
                if state['fills'][fill_id] != fill:
                    raise DataQualityError('PAPER_FILL_CONFLICT')
                return state['fills'][fill_id]
            order = state['orders'].get(client_order_id)
            if order is None or order['status'] not in ('OPEN', 'PARTIALLY_FILLED'):
                raise DataQualityError('PAPER_FILL_ORDER_NOT_OPEN')
            p = order['request']
            remaining = Decimal(p['quantity']) - Decimal(order['filled_quantity'])
            buy = p['side'] == 'BUY'
            if quantity > remaining or (buy and price > Decimal(p['limit_price'])) or (not buy and price < Decimal(p['limit_price'])):
                raise DataQualityError('PAPER_FILL_LIMIT_OR_QUANTITY')
            if fee > Decimal(order['remaining_fee']):
                raise DataQualityError('PAPER_FILL_FEE_EXCEEDS_RESERVE')
            instrument = p['instrument_id']
            state['cash'] = str(Decimal(state['cash']) + (-quantity * price if buy else quantity * price) - fee)
            state['positions'][instrument] = str(Decimal(state['positions'].get(instrument, '0')) + (quantity if buy else -quantity))
            order['filled_quantity'] = str(Decimal(order['filled_quantity']) + quantity)
            order['remaining_fee'] = str(Decimal(order['remaining_fee']) - fee)
            order['status'] = 'FILLED' if quantity == remaining else 'PARTIALLY_FILLED'
            state['fills'][fill_id] = fill
            self._save(state)
            return fill

    def get_balances(self):
        state = self._read()
        reserved, _ = self._reserved(state)
        return dict(account=self.account, currency=state['currency'], cash=state['cash'],
                    reserved_cash=str(reserved), available_cash=str(Decimal(state['cash']) - reserved))

    def get_positions(self):
        state = self._read()
        _, reserved = self._reserved(state)
        return [dict(instrument_id=k, quantity=v, available_quantity=str(Decimal(v) - reserved.get(k, Decimal(0))))
                for k, v in sorted(state['positions'].items())]

    def get_fills(self):
        return list(self._read()['fills'].values())
