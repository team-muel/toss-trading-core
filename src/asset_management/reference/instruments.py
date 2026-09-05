"""Canonical IDs are opaque UUIDs, independent of ticker changes."""
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from asset_management.domain.errors import DataQualityError
from .history import ReferenceHistory


class InstrumentRepository(ReferenceHistory):
    def register(self, *, instrument_id=None, ticker, toss_symbol, vendor_symbol,
                 cik, mic, asset_class, currency, timezone, **history):
        try:
            identifier = str(UUID(instrument_id)) if instrument_id else str(uuid4())
            ZoneInfo(timezone)
        except (ValueError, AttributeError, ZoneInfoNotFoundError) as error:
            raise DataQualityError('INSTRUMENT_ID_OR_TIMEZONE_INVALID') from error
        if not all(isinstance(x, str) and x.strip() for x in
                   (ticker, toss_symbol, vendor_symbol, mic, asset_class, currency)):
            raise DataQualityError('INSTRUMENT_METADATA_MISSING')
        self.append('INSTRUMENT', identifier, dict(instrument_id=identifier,
                    ticker=ticker, toss_symbol=toss_symbol, vendor_symbol=vendor_symbol,
                    cik=cik, mic=mic, asset_class=asset_class, currency=currency,
                    timezone=timezone), **history)
        return identifier

    def get(self, instrument_id, context):
        result = self.active('INSTRUMENT', context).get(instrument_id)
        if result is None:
            raise DataQualityError('INSTRUMENT_NOT_LISTED_AT_ASOF')
        if any(action['instrument_id'] == instrument_id and action['action_type'] == 'DELISTING'
               for action in self.active('ACTION', context).values()):
            raise DataQualityError('INSTRUMENT_DELISTED')
        return result
