"""Append-only storage contracts for normalized point-in-time observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from asset_management.domain.errors import InvariantViolation, TemporalViolation


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise TemporalViolation(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _reject_float(value: object, path: str = "value") -> None:
    if isinstance(value, float):
        raise TypeError(f"{path} must use an exact decimal string, not float")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            _reject_float(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_float(item, f"{path}[{index}]")


def canonical_json(value: object) -> str:
    _reject_float(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise TypeError("observation value must be canonical JSON data") from exc


@dataclass(frozen=True, slots=True)
class TemporalObservation:
    observation_id: str
    entity_id: str
    field: str
    value: Any
    reference_period: str
    event_time: datetime
    scheduled_release_at: datetime | None
    official_release_at: datetime | None
    source_timestamp: datetime
    received_at: datetime
    available_at: datetime
    ingested_at: datetime
    revised_at: datetime | None
    source_timezone: str
    schema_version: str
    raw_response_id: str | None = None
    dataset_manifest_id: str | None = None
    supersedes_observation_id: str | None = None
    content_hash: str = ""

    def __post_init__(self) -> None:
        for name in (
            "observation_id", "entity_id", "field", "reference_period",
            "source_timezone", "schema_version",
        ):
            if not getattr(self, name).strip():
                raise InvariantViolation(f"{name} cannot be blank")
        try:
            ZoneInfo(self.source_timezone)
        except ZoneInfoNotFoundError as exc:
            raise TemporalViolation("source_timezone must be an IANA timezone") from exc
        for name in (
            "event_time", "scheduled_release_at", "official_release_at",
            "source_timestamp", "received_at", "available_at", "ingested_at", "revised_at",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _utc(value, name))
        if (self.raw_response_id is None) == (self.dataset_manifest_id is None):
            raise InvariantViolation(
                "exactly one raw_response_id or dataset_manifest_id is required"
            )
        if self.received_at < self.source_timestamp:
            raise TemporalViolation("received_at cannot precede source_timestamp")
        if self.ingested_at < self.received_at:
            raise TemporalViolation("ingested_at cannot precede received_at")
        lower_bounds = [self.source_timestamp, self.received_at, self.ingested_at]
        lower_bounds.extend(
            item for item in (self.official_release_at, self.revised_at) if item is not None
        )
        if self.available_at < max(lower_bounds):
            raise TemporalViolation(
                "available_at cannot precede publication, receipt, ingestion, or revision"
            )
        if (self.supersedes_observation_id is None) != (self.revised_at is None):
            raise InvariantViolation(
                "a revision requires both revised_at and supersedes_observation_id"
            )
        canonical_json(self.value)
        expected = observation_hash(self)
        if self.content_hash and self.content_hash != expected:
            raise InvariantViolation("observation content hash mismatch")
        object.__setattr__(self, "content_hash", expected)


def observation_hash(value: TemporalObservation) -> str:
    payload = {
        "entity_id": value.entity_id,
        "field": value.field,
        "value": value.value,
        "reference_period": value.reference_period,
        "event_time": value.event_time.isoformat(),
        "scheduled_release_at": value.scheduled_release_at.isoformat() if value.scheduled_release_at else None,
        "official_release_at": value.official_release_at.isoformat() if value.official_release_at else None,
        "source_timestamp": value.source_timestamp.isoformat(),
        "received_at": value.received_at.isoformat(),
        "available_at": value.available_at.isoformat(),
        "ingested_at": value.ingested_at.isoformat(),
        "revised_at": value.revised_at.isoformat() if value.revised_at else None,
        "source_timezone": value.source_timezone,
        "schema_version": value.schema_version,
        "raw_response_id": value.raw_response_id,
        "dataset_manifest_id": value.dataset_manifest_id,
        "supersedes_observation_id": value.supersedes_observation_id,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class SQLiteTemporalObservationStore:
    """Writes immutable observations and validates their source lineage by FK."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.execute("PRAGMA foreign_keys=ON")

    def append(self, **values: object) -> TemporalObservation:
        observation = TemporalObservation(
            observation_id=str(values.pop("observation_id", "") or uuid4()), **values
        )
        existing = self._conn.execute(
            "SELECT content_hash FROM am_temporal_observation WHERE observation_id = ?",
            (observation.observation_id,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) == observation.content_hash:
                return observation
            raise InvariantViolation("observation id was reused with different content")
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO am_temporal_observation (
                      observation_id, entity_id, field_name, value_json, reference_period,
                      event_time_utc, scheduled_release_at_utc, official_release_at_utc,
                      source_timestamp_utc, received_at_utc, available_at_utc, ingested_at_utc,
                      revised_at_utc, source_timezone, schema_version, raw_response_id,
                      dataset_manifest_id, supersedes_observation_id, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _row_values(observation),
                )
        except sqlite3.IntegrityError as exc:
            raise InvariantViolation(f"invalid point-in-time observation: {exc}") from exc
        return observation


def _row_values(value: TemporalObservation) -> tuple[object, ...]:
    stamp = lambda item: item.isoformat() if item is not None else None
    return (
        value.observation_id, value.entity_id, value.field, canonical_json(value.value),
        value.reference_period, stamp(value.event_time), stamp(value.scheduled_release_at),
        stamp(value.official_release_at), stamp(value.source_timestamp), stamp(value.received_at),
        stamp(value.available_at), stamp(value.ingested_at), stamp(value.revised_at),
        value.source_timezone, value.schema_version, value.raw_response_id,
        value.dataset_manifest_id, value.supersedes_observation_id, value.content_hash,
    )


def observation_from_row(row: sqlite3.Row | tuple[object, ...]) -> TemporalObservation:
    optional_time = lambda item: datetime.fromisoformat(str(item)) if item is not None else None
    return TemporalObservation(
        observation_id=str(row[0]), entity_id=str(row[1]), field=str(row[2]),
        value=json.loads(str(row[3])), reference_period=str(row[4]),
        event_time=datetime.fromisoformat(str(row[5])),
        scheduled_release_at=optional_time(row[6]), official_release_at=optional_time(row[7]),
        source_timestamp=datetime.fromisoformat(str(row[8])),
        received_at=datetime.fromisoformat(str(row[9])),
        available_at=datetime.fromisoformat(str(row[10])),
        ingested_at=datetime.fromisoformat(str(row[11])), revised_at=optional_time(row[12]),
        source_timezone=str(row[13]), schema_version=str(row[14]),
        raw_response_id=str(row[15]) if row[15] is not None else None,
        dataset_manifest_id=str(row[16]) if row[16] is not None else None,
        supersedes_observation_id=str(row[17]) if row[17] is not None else None,
        content_hash=str(row[18]),
    )
