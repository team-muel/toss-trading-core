"""Provider-qualified aliases with historical identity resolution."""
from asset_management.domain.errors import DataQualityError
from .instruments import InstrumentRepository


class AliasRepository(InstrumentRepository):
    def add(self, *, alias_id, instrument_id, alias_type, alias_value, **history):
        if not alias_type.strip() or not alias_value.strip():
            raise DataQualityError('ALIAS_MISSING')
        return self.append('ALIAS', alias_id, dict(instrument_id=instrument_id,
                           alias_type=alias_type, alias_value=alias_value), **history)

    def resolve(self, alias_type, alias_value, context):
        matches = {item['instrument_id'] for item in self.active('ALIAS', context).values()
                   if (item['alias_type'], item['alias_value']) == (alias_type, alias_value)}
        if len(matches) != 1:
            raise DataQualityError('ALIAS_MISSING_OR_AMBIGUOUS')
        identifier = next(iter(matches))
        self.get(identifier, context)
        return identifier

    def canonicalize_order(self, order, context):
        identifier = self.resolve('toss_symbol', order['symbol'], context)
        if order.get('instrumentId', identifier) != identifier:
            raise DataQualityError('BROKER_CANONICAL_ID_CONFLICT')
        return dict(order, instrumentId=identifier)
