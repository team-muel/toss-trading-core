"""Immutable bitemporal reference versions; intervals are half-open."""
from datetime import datetime
from hashlib import sha256
import json
import sqlite3
from uuid import uuid4

from asset_management.domain.errors import DataQualityError
from asset_management.time.asof import require_as_of_context
from asset_management.time.timezone import utc


class ReferenceHistory:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def append(self, kind, key, payload, *, effective_from, effective_to=None,
               available_at, source):
        start, known = utc(effective_from), utc(available_at)
        end = utc(effective_to) if effective_to is not None else None
        if not key.strip() or not source.strip() or (end is not None and end <= start):
            raise DataQualityError('REFERENCE_INVALID_INTERVAL_OR_SOURCE')
        def validate(value):
            if isinstance(value, float):
                raise DataQualityError('REFERENCE_FLOAT_FORBIDDEN')
            if isinstance(value, dict):
                for child in value.values():
                    validate(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    validate(child)
        validate(payload)
        if kind in {'ALIAS', 'UNIVERSE', 'ACTION'}:
            if self.conn.execute(
                    "SELECT 1 FROM am_reference_record WHERE kind='INSTRUMENT' AND entity_key=? AND available_at<=?",
                    (payload['instrument_id'], known.isoformat())).fetchone() is None:
                raise DataQualityError('REFERENCE_INSTRUMENT_MISSING_AT_KNOWLEDGE_TIME')
        body = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        values = (kind, key, start.isoformat(), end.isoformat() if end else None,
                  known.isoformat(), source, body)
        digest = sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()
        existing = self.conn.execute(
            'SELECT record_id,content_hash FROM am_reference_record WHERE kind=? AND entity_key=? AND available_at=?',
            (kind, key, known.isoformat())).fetchone()
        if existing:
            if existing[1] != digest:
                raise DataQualityError('REFERENCE_CONFLICT')
            return existing[0]
        identifier = str(uuid4())
        with self.conn:
            self.conn.execute('INSERT INTO am_reference_record VALUES (?,?,?,?,?,?,?,?,?)',
                              (identifier, *values, digest))
        return identifier

    def versions(self, kind, context):
        require_as_of_context(context)
        rows = self._known_rows(kind, context)
        result = {}
        for row in rows:
            if row[1] not in result:
                result[row[1]] = self._version(row)
        return result

    def effective(self, kind, effective_at, context):
        """Select latest knowledge among versions effective at the requested instant."""
        require_as_of_context(context)
        instant = utc(effective_at)
        result = {}
        considered = set()
        for row in self._known_rows(kind, context):
            start, end, body = self._version(row)
            key = row[1]
            if key in considered or start > instant:
                continue
            considered.add(key)
            if end is None or instant < end:
                result[key] = body
        return result

    def _known_rows(self, kind, context):
        return self.conn.execute(
            '''SELECT kind,entity_key,effective_from,effective_to,available_at,source,payload_json,content_hash
               FROM am_reference_record WHERE kind=? AND available_at<=?
               ORDER BY entity_key,available_at DESC''',
            (kind, context.information_cutoff_utc.isoformat())).fetchall()

    @staticmethod
    def _version(row):
            digest = sha256(json.dumps(tuple(row[:7]), separators=(',', ':')).encode()).hexdigest()
            if digest != row[7]:
                raise DataQualityError('REFERENCE_HASH_MISMATCH')
            return (datetime.fromisoformat(row[2]),
                    datetime.fromisoformat(row[3]) if row[3] else None,
                    json.loads(row[6]))

    def active(self, kind, context):
        return self.effective(kind, context.as_of_utc, context)
