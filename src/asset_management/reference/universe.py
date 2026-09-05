"""Historical membership is intersected with listing validity."""
from asset_management.domain.errors import DataQualityError
from .instruments import InstrumentRepository


class UniverseRepository(InstrumentRepository):
    def include(self, *, membership_id, universe_id, instrument_id, inclusion_reason, **history):
        if not universe_id.strip() or not inclusion_reason.strip():
            raise DataQualityError('UNIVERSE_REASON_MISSING')
        return self.append('UNIVERSE', membership_id, dict(universe_id=universe_id,
                           instrument_id=instrument_id, inclusion_reason=inclusion_reason), **history)

    def members(self, universe_id, context):
        versions = self.versions('UNIVERSE', context)
        if not any(body['universe_id'] == universe_id for _, _, body in versions.values()):
            raise DataQualityError('UNIVERSE_HISTORY_MISSING')
        listed = self.active('INSTRUMENT', context)
        delisted = {a['instrument_id'] for a in self.active('ACTION', context).values()
                    if a['action_type'] == 'DELISTING'}
        return tuple(sorted({item['instrument_id'] for item in self.active('UNIVERSE', context).values()
                             if item['universe_id'] == universe_id and item['instrument_id'] in listed
                             and item['instrument_id'] not in delisted}))
