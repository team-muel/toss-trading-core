"""Bind Toss order constraints to verified raw evidence and independent cash truth."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from toss_trading.contracts.toss import TossContractError, require_buying_power
from asset_management.broker.contracts import require_decimal_string
from asset_management.data.immutable import canonical, digest
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.errors import DataQualityError, ReconciliationError
from .cash import BrokerConstraint, CashLedger


def cash_state_from_buying_power(conn, *, source_response_id: str, account_id: str,
                                 currency: str, as_of_utc: datetime, max_age: timedelta,
                                 operational_liquidity_reserve: Decimal, policy_version: str):
    """No cash opening is inferred from buying power; session/order gates remain separate."""
    if (any(not isinstance(value, str) or not value.strip() for value in
            (source_response_id, account_id, policy_version)) or currency not in ('USD', 'KRW')):
        raise ReconciliationError('CASH_CONSTRAINT_CONTEXT_REQUIRED')
    if (not isinstance(as_of_utc, datetime) or as_of_utc.tzinfo is None or as_of_utc.utcoffset() is None
            or not isinstance(max_age, timedelta) or max_age <= timedelta(0)):
        raise ReconciliationError('CASH_CONSTRAINT_TIME_POLICY_INVALID')
    reserve = operational_liquidity_reserve
    if not isinstance(reserve, Decimal) or not reserve.is_finite() or reserve < 0:
        raise ReconciliationError('CASH_OPERATIONAL_RESERVE_INVALID')
    as_of = as_of_utc.astimezone(timezone.utc)
    try:
        raw = SQLiteRawResponseStore(conn).verified(source_response_id)
    except (KeyError, ValueError, TypeError) as error:
        raise ReconciliationError('CASH_CONSTRAINT_RAW_EVIDENCE_INVALID') from error
    if (raw.source != 'toss' or raw.endpoint != '/api/v1/buying-power' or raw.http_method != 'GET'
            or raw.status_code != 200 or raw.account_id != account_id or raw.schema_version != '1.2.14'):
        raise ReconciliationError('CASH_CONSTRAINT_SOURCE_CONTEXT_MISMATCH')
    if (raw.requested_at.tzinfo is None or raw.received_at.tzinfo is None or
            not raw.requested_at <= raw.received_at <= as_of or as_of - raw.requested_at > max_age):
        raise ReconciliationError('CASH_CONSTRAINT_FUTURE_OR_STALE')
    try:
        result = require_buying_power(raw.body)
        if result['currency'] != currency:
            raise ReconciliationError('CASH_CONSTRAINT_CURRENCY_MISMATCH')
        value = require_decimal_string(result['cashBuyingPower'], 'cashBuyingPower')
        if value < 0:
            raise ReconciliationError('CASH_CONSTRAINT_NEGATIVE')
    except (TossContractError, DataQualityError) as error:
        raise ReconciliationError('CASH_CONSTRAINT_RESPONSE_INVALID') from error
    constraint = BrokerConstraint(value, raw.received_at, raw.requested_at + max_age, source_response_id)
    state = CashLedger(conn).state(account_id=account_id, currency=currency, as_of_utc=as_of,
                                    broker_buying_power_constraint=constraint)
    free_cash = state.available_cash - reserve
    if free_cash < 0:
        raise ReconciliationError('CASH_OPERATIONAL_RESERVE_EXCEEDS_AVAILABLE')
    body = dict(schema_version='cash-constraint-evidence@1', account_id=account_id,
                currency=currency, as_of_utc=as_of.isoformat(), policy_version=policy_version,
                formula_version='cash-constraint@1', source_response_id=source_response_id,
                response_hash=raw.response_hash, request_hash=raw.request_hash,
                provider_contract_version=raw.schema_version,
                requested_at=raw.requested_at.isoformat(), received_at=raw.received_at.isoformat(),
                valid_until=(raw.requested_at + max_age).isoformat(),
                settled_cash=str(state.settled_cash), unsettled_cash=str(state.unsettled_cash),
                reserved_cash=str(state.reserved_cash), operational_liquidity_reserve=str(reserve),
                internal_free_cash=str(free_cash), broker_buying_power=str(value),
                cash_constrained_buy_limit=str(min(free_cash, value)), unit='MONEY')
    return body | {'content_hash': digest(canonical(body))}
