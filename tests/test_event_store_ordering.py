from dataclasses import replace
from datetime import datetime, timedelta, timezone
import itertools
import json
from pathlib import Path
import sqlite3
import pytest
from asset_management.domain.errors import DataQualityError
from asset_management.replay.event_store import EventEnvelope, EventStore, EventType
from asset_management.replay.ordering import order_events

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def event(**changes):
    data = dict(event_type=EventType.MARKET_BAR_CLOSED, source='market', sequence_number=1,
                event_time=NOW, received_at=NOW, available_at=NOW, payload_json='{"price":"100"}')
    return EventEnvelope(**(data | changes))


@pytest.mark.parametrize('kind', list(EventType))
def test_all_event_types_roundtrip_and_duplicate_delivery(tmp_path, kind):
    path = tmp_path/'events.sqlite'
    store = EventStore(path)
    row = event(event_type=kind)
    assert store.append(row) == store.append(row)
    store.close()
    reopened = EventStore(path)
    assert reopened.read(cutoff=NOW) == (row,)
    assert reopened.read(cutoff=NOW-timedelta(microseconds=1)) == ()
    reopened.close()


def test_conflicting_sequence_never_overwrites_original(tmp_path):
    store = EventStore(tmp_path/'events.sqlite')
    original = event()
    store.append(original)
    with pytest.raises(DataQualityError, match='EVENT_SEQUENCE_CONFLICT'):
        store.append(replace(original, payload_json='{"price":"999"}'))
    assert store.read(cutoff=NOW) == (original,)
    for command in ('DELETE FROM replay_event', "UPDATE replay_event SET source='other'"):
        with pytest.raises(sqlite3.IntegrityError, match='immutable event'):
            store._conn.execute(command)
    store.close()


def test_order_is_independent_of_arrival_permutation():
    a = event(source='high', sequence_number=2)
    b = event(source='high', sequence_number=3)
    c = event(source='low', sequence_number=0)
    d = event(source='high', sequence_number=0, available_at=NOW+timedelta(seconds=1))
    expected = (a,b,c,d)
    for permutation in itertools.permutations(expected):
        assert order_events(permutation, source_priorities={'high':0,'low':1}) == expected


def test_receipt_precedes_priority_and_ambiguous_ties_fail():
    later = event(source='high', received_at=NOW+timedelta(seconds=1), available_at=NOW+timedelta(seconds=2))
    earlier = event(source='low', available_at=NOW+timedelta(seconds=2))
    assert order_events((later,earlier), source_priorities={'high':0,'low':1}) == (earlier,later)
    with pytest.raises(DataQualityError, match='EVENT_ORDER_AMBIGUOUS'):
        order_events((event(source='a'),event(source='b')), source_priorities={'a':0,'b':0})
    with pytest.raises(DataQualityError, match='EVENT_DUPLICATE_SEQUENCE'):
        order_events((event(),event()), source_priorities={'market':0})
    with pytest.raises(DataQualityError, match='EVENT_SOURCE_PRIORITY_MISSING'):
        order_events((event(),), source_priorities={'other':0})


@pytest.mark.parametrize('changes', [
    {'payload_json':'{}'}, {'payload_json':'{"a":1,"a":2}'}, {'payload_json':'{"a":NaN}'},
    {'sequence_number':True}, {'event_type':'UNKNOWN'}, {'received_at':NOW-timedelta(seconds=1)},
    {'available_at':NOW.replace(tzinfo=None)},
])
def test_malformed_envelope_fails_closed(changes):
    with pytest.raises(DataQualityError):
        event(**changes)


def test_tampered_storage_is_detected(tmp_path):
    store = EventStore(tmp_path/'events.sqlite')
    store.append(event())
    store._conn.execute('DROP TRIGGER replay_event_no_update')
    store._conn.execute("UPDATE replay_event SET body='{}'")
    with pytest.raises(DataQualityError, match='EVENT_STORE_CORRUPT'):
        store.read(cutoff=NOW)
    store.close()


def test_envelope_schema_and_payload_normalization():
    schema = json.loads((Path(__file__).parents[1]/'schemas/event_envelope.schema.json').read_text())
    assert set(schema['required']) == set(event().payload())
    assert set(schema['properties']['event_type']['enum']) == set(EventType)
    assert event(payload_json=' {"price": "100"} ').event_id == event().event_id
