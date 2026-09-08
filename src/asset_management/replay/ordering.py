"""Replay order is availability, receipt, configured source priority, then producer sequence."""
from collections.abc import Mapping
from asset_management.domain.errors import DataQualityError
from .event_store import EventEnvelope


def order_events(events, *, source_priorities: Mapping[str, int]) -> tuple[EventEnvelope, ...]:
    if (not isinstance(source_priorities, Mapping) or not source_priorities or
            any(not isinstance(k, str) or not k.strip() or type(v) is not int or v < 0
                for k, v in source_priorities.items())):
        raise DataQualityError('EVENT_SOURCE_PRIORITY_INVALID')
    rows = tuple(events)
    if any(not isinstance(e, EventEnvelope) for e in rows):
        raise DataQualityError('EVENT_ENVELOPE_REQUIRED')
    if len({(e.source, e.sequence_number) for e in rows}) != len(rows):
        raise DataQualityError('EVENT_DUPLICATE_SEQUENCE')
    if any(e.source not in source_priorities for e in rows):
        raise DataQualityError('EVENT_SOURCE_PRIORITY_MISSING')
    def key(e):
        return e.available_at, e.received_at, source_priorities[e.source], e.sequence_number
    if len({key(e) for e in rows}) != len(rows):
        raise DataQualityError('EVENT_ORDER_AMBIGUOUS')
    return tuple(sorted(rows, key=key))
