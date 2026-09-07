"""Immutable SQLite event envelopes with content verification on every read."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
import sqlite3

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import DataQualityError


class EventType(StrEnum):
    MARKET_BAR_CLOSED = "MARKET_BAR_CLOSED"
    MACRO_RELEASED = "MACRO_RELEASED"
    MACRO_REVISED = "MACRO_REVISED"
    FILING_ACCEPTED = "FILING_ACCEPTED"
    EARNINGS_RELEASED = "EARNINGS_RELEASED"
    ESTIMATE_UPDATED = "ESTIMATE_UPDATED"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    ACCOUNT_SNAPSHOT = "ACCOUNT_SNAPSHOT"
    ORDER_ACK = "ORDER_ACK"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"
    SOURCE_DEGRADED = "SOURCE_DEGRADED"
    SOURCE_RECOVERED = "SOURCE_RECOVERED"
    POLICY_ACTIVATED = "POLICY_ACTIVATED"


def utc_stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataQualityError("EVENT_TIME_INVALID")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_type: EventType
    source: str
    sequence_number: int
    event_time: datetime
    received_at: datetime
    available_at: datetime
    payload_json: str

    def __post_init__(self):
        if (not isinstance(self.event_type, EventType) or not isinstance(self.source, str) or
                not self.source.strip() or type(self.sequence_number) is not int or
                not 0 <= self.sequence_number <= 9223372036854775807):
            raise DataQualityError("EVENT_IDENTITY_INVALID")
        for name in ('event_time', 'received_at', 'available_at'):
            object.__setattr__(self, name, utc_stamp(getattr(self, name)))
        if not self.event_time <= self.received_at <= self.available_at:
            raise DataQualityError("EVENT_TIME_ORDER_INVALID")
        try:
            # Reject ambiguous duplicate keys before canonicalization.
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('duplicate key')
                    result[key] = value
                return result
            payload = json.loads(self.payload_json, object_pairs_hook=unique)
            if not isinstance(payload, dict) or not payload:
                raise ValueError('nonempty payload required')
            normalized = canonical(payload).decode('utf-8')
        except (TypeError, ValueError) as exc:
            raise DataQualityError("EVENT_PAYLOAD_INVALID") from exc
        object.__setattr__(self, 'payload_json', normalized)

    def body(self):
        return dict(event_type=self.event_type.value, source=self.source, sequence_number=self.sequence_number,
                    event_time=self.event_time.isoformat(), received_at=self.received_at.isoformat(),
                    available_at=self.available_at.isoformat(), payload=json.loads(self.payload_json))

    @property
    def event_id(self):
        return digest(canonical(self.body()))

    def payload(self):
        return dict(event_id=self.event_id, **self.body())


class EventStore:
    """Own a dedicated event database connection; sequences come from durable producer evidence."""

    def __init__(self, path):
        self._conn = sqlite3.connect(path)
        self._conn.execute('PRAGMA synchronous=FULL')
        self._conn.executescript('''
            CREATE TABLE IF NOT EXISTS replay_event (
                event_id TEXT PRIMARY KEY, source TEXT NOT NULL,
                sequence_number INTEGER NOT NULL, body TEXT NOT NULL,
                UNIQUE(source, sequence_number));
            CREATE TRIGGER IF NOT EXISTS replay_event_no_update BEFORE UPDATE ON replay_event
                BEGIN SELECT RAISE(ABORT, 'immutable event'); END;
            CREATE TRIGGER IF NOT EXISTS replay_event_no_delete BEFORE DELETE ON replay_event
                BEGIN SELECT RAISE(ABORT, 'immutable event'); END;
        ''')

    def close(self):
        self._conn.close()

    def append(self, event: EventEnvelope) -> str:
        if not isinstance(event, EventEnvelope):
            raise DataQualityError('EVENT_ENVELOPE_REQUIRED')
        body = canonical(event.body()).decode('utf-8')
        with self._conn:
            self._conn.execute('INSERT OR IGNORE INTO replay_event VALUES (?, ?, ?, ?)',
                               (event.event_id, event.source, event.sequence_number, body))
            row = self._conn.execute('SELECT event_id, body FROM replay_event WHERE source=? AND sequence_number=?',
                                     (event.source, event.sequence_number)).fetchone()
            if row != (event.event_id, body):
                raise DataQualityError('EVENT_SEQUENCE_CONFLICT')
        return event.event_id

    def read(self, *, cutoff: datetime) -> tuple[EventEnvelope, ...]:
        cutoff = utc_stamp(cutoff)
        events = []
        for identifier, source, sequence, body in self._conn.execute('SELECT * FROM replay_event'):
            try:
                data = json.loads(body)
                event = EventEnvelope(EventType(data['event_type']), data['source'], data['sequence_number'],
                                      datetime.fromisoformat(data['event_time']), datetime.fromisoformat(data['received_at']),
                                      datetime.fromisoformat(data['available_at']), json.dumps(data['payload']))
                if (event.event_id != identifier or event.source != source or event.sequence_number != sequence or
                        canonical(event.body()).decode('utf-8') != body):
                    raise ValueError('hash mismatch')
            except (ValueError, TypeError, KeyError, DataQualityError) as exc:
                raise DataQualityError('EVENT_STORE_CORRUPT') from exc
            if event.available_at <= cutoff:
                events.append(event)
        return tuple(events)
