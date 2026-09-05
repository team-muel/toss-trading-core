"""Explicit price basis prevents adjusted-price and cash-dividend double counting."""
from enum import StrEnum
from asset_management.domain.decimal import exact_decimal
from asset_management.domain.errors import DataQualityError
from asset_management.reference.instruments import InstrumentRepository
from asset_management.time.asof import require_as_of_context
from asset_management.time.timezone import utc
from .repositories import SQLiteTemporalObservationStore


class PriceBasis(StrEnum):
    RAW = 'raw'
    SPLIT_ADJUSTED = 'split_adjusted'
    TOTAL_RETURN = 'total_return'


def require_price_basis(bases, *, cash_dividends=False, ledger=False):
    values = {PriceBasis(value) for value in bases}
    if len(values) != 1:
        raise DataQualityError('PRICE_BASIS_MIXED_OR_MISSING')
    basis = next(iter(values))
    if ledger and basis != PriceBasis.RAW:
        raise DataQualityError('LEDGER_REQUIRES_RAW_PRICE')
    if cash_dividends and basis == PriceBasis.TOTAL_RETURN:
        raise DataQualityError('DIVIDEND_DOUBLE_COUNT')
    return basis


def calculate_price_return(start, end, *, context, cash_dividend='0'):
    """Compute a same-instrument return from two stored PIT observations."""
    require_as_of_context(context)
    for observation in (start, end):
        context.require_known_at(observation.available_at)
        if observation.event_time > context.as_of_utc:
            raise DataQualityError('PRICE_EVENT_IN_FUTURE')
    if start.entity_id != end.entity_id or start.event_time >= end.event_time:
        raise DataQualityError('PRICE_SERIES_OR_ORDER_CONFLICT')
    dividend = exact_decimal(cash_dividend)
    if dividend < 0:
        raise DataQualityError('DIVIDEND_NEGATIVE')
    if any(not point.field.startswith('price:') for point in (start, end)):
        raise DataQualityError('PRICE_BASIS_MISSING')
    require_price_basis([point.field.removeprefix('price:') for point in (start, end)],
                        cash_dividends=dividend != 0)
    initial, final = exact_decimal(start.value), exact_decimal(end.value)
    if initial <= 0 or final <= 0:
        raise DataQualityError('PRICE_NOT_POSITIVE')
    return (final + dividend) / initial - 1


class PriceObservationStore:
    def __init__(self, conn):
        self.instruments = InstrumentRepository(conn)
        self.observations = SQLiteTemporalObservationStore(conn)

    def append(self, *, instrument_id, basis, price, context, **observation):
        require_as_of_context(context)
        basis = PriceBasis(basis)
        value = exact_decimal(price)
        if value <= 0:
            raise DataQualityError('PRICE_NOT_POSITIVE')
        versions = self.instruments.versions('INSTRUMENT', context)
        if instrument_id not in versions:
            raise DataQualityError('INSTRUMENT_UNKNOWN')
        start, end, _ = versions[instrument_id]
        event = utc(observation['event_time'])
        if event < start or (end is not None and event >= end):
            raise DataQualityError('PRICE_OUTSIDE_LISTING')
        for action_start, _, action in self.instruments.versions('ACTION', context).values():
            if action['instrument_id'] == instrument_id and action['action_type'] == 'DELISTING' and event >= action_start:
                raise DataQualityError('PRICE_AFTER_DELISTING')
        context.require_known_at(utc(observation['available_at']))
        return self.observations.append(entity_id=instrument_id, field='price:' + basis.value,
                                        value=str(value), **observation)
