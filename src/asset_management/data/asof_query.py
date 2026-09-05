"""Fail-closed queries constrained by an explicit AsOfContext."""

from __future__ import annotations

import sqlite3

from asset_management.data.repositories import TemporalObservation, observation_from_row
from asset_management.domain.errors import DataQualityError
from asset_management.time.asof import AsOfContext, require_as_of_context


_COLUMNS = """
observation_id, entity_id, field_name, value_json, reference_period,
event_time_utc, scheduled_release_at_utc, official_release_at_utc,
source_timestamp_utc, received_at_utc, available_at_utc, ingested_at_utc,
revised_at_utc, source_timezone, schema_version, raw_response_id,
dataset_manifest_id, supersedes_observation_id, content_hash
"""


class AsOfRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_latest(
        self, *, entity_id: str, field: str, context: AsOfContext,
        dataset_manifest_id: str | None = None,
    ) -> TemporalObservation:
        context = require_as_of_context(context)
        if not entity_id.strip() or not field.strip():
            raise DataQualityError("entity_id and field are required")
        manifest_clause = " AND dataset_manifest_id = ?" if dataset_manifest_id else ""
        parameters = [entity_id, field, context.information_cutoff_utc.isoformat()]
        if dataset_manifest_id:
            parameters.append(dataset_manifest_id)
        rows = self._conn.execute(
            f"""
            SELECT {_COLUMNS}
            FROM am_temporal_observation
            WHERE entity_id = ? AND field_name = ? AND available_at_utc <= ?
              {manifest_clause}
            ORDER BY available_at_utc DESC, event_time_utc DESC,
                     reference_period DESC, observation_id DESC
            LIMIT 2
            """,
            tuple(parameters),
        ).fetchall()
        if not rows:
            raise DataQualityError(
                f"MISSING: no {entity_id}.{field} observation was available at cutoff"
            )
        first = observation_from_row(rows[0])
        context.require_known_at(first.available_at, label=f"{entity_id}.{field}")
        if len(rows) > 1:
            second = observation_from_row(rows[1])
            first_key = (first.available_at, first.event_time, first.reference_period)
            second_key = (second.available_at, second.event_time, second.reference_period)
            if first_key == second_key and first.content_hash != second.content_hash:
                raise DataQualityError(
                    f"CONFLICT: ambiguous {entity_id}.{field} observations at cutoff"
                )
        return first

    def series(
        self, *, entity_id: str, field: str, context: AsOfContext,
        dataset_manifest_id: str | None = None,
    ) -> tuple[TemporalObservation, ...]:
        """Return one latest-known vintage per period, ordered by event time.

        A manifest filter binds the read to the immutable dataset selected by the
        caller. Revisions unavailable at the information cutoff remain invisible.
        """
        context = require_as_of_context(context)
        if not entity_id.strip() or not field.strip():
            raise DataQualityError("entity_id and field are required")
        manifest_clause = " AND dataset_manifest_id = ?" if dataset_manifest_id else ""
        parameters = [entity_id, field, context.information_cutoff_utc.isoformat()]
        if dataset_manifest_id:
            parameters.append(dataset_manifest_id)
        rows = self._conn.execute(
            f"""
            SELECT {_COLUMNS} FROM am_temporal_observation
            WHERE entity_id = ? AND field_name = ? AND available_at_utc <= ?
              {manifest_clause}
            ORDER BY reference_period, available_at_utc DESC, observation_id DESC
            """,
            tuple(parameters),
        ).fetchall()
        latest: dict[str, TemporalObservation] = {}
        for row in rows:
            observation = observation_from_row(row)
            current = latest.get(observation.reference_period)
            if current is None:
                context.require_known_at(
                    observation.available_at, label=f"{entity_id}.{field}"
                )
                latest[observation.reference_period] = observation
            elif current.available_at == observation.available_at \
                    and current.content_hash != observation.content_hash:
                raise DataQualityError(
                    f"CONFLICT: ambiguous {entity_id}.{field} vintage at cutoff"
                )
        if not latest:
            raise DataQualityError(
                f"MISSING: no {entity_id}.{field} series was available at cutoff"
            )
        return tuple(sorted(
            latest.values(),
            key=lambda item: (item.event_time, item.reference_period, item.observation_id),
        ))

    def get_vintage(
        self, *, entity_id: str, field: str, reference_period: str,
        context: AsOfContext,
    ) -> TemporalObservation:
        context = require_as_of_context(context)
        rows = self._conn.execute(
            f"""
            SELECT {_COLUMNS}
            FROM am_temporal_observation
            WHERE entity_id = ? AND field_name = ? AND reference_period = ?
              AND available_at_utc <= ?
            ORDER BY available_at_utc DESC, observation_id DESC
            LIMIT 2
            """,
            (entity_id, field, reference_period, context.information_cutoff_utc.isoformat()),
        ).fetchall()
        if not rows:
            raise DataQualityError(
                f"MISSING: no vintage for {entity_id}.{field}/{reference_period} at cutoff"
            )
        first = observation_from_row(rows[0])
        context.require_known_at(first.available_at, label=f"{entity_id}.{field}")
        if len(rows) > 1 and first.available_at == observation_from_row(rows[1]).available_at:
            raise DataQualityError("CONFLICT: multiple vintages share the latest availability")
        return first

    def history(
        self, *, entity_id: str, field: str, reference_period: str,
        context: AsOfContext,
    ) -> tuple[TemporalObservation, ...]:
        context = require_as_of_context(context)
        rows = self._conn.execute(
            f"""
            SELECT {_COLUMNS} FROM am_temporal_observation
            WHERE entity_id = ? AND field_name = ? AND reference_period = ?
              AND available_at_utc <= ?
            ORDER BY available_at_utc, observation_id
            """,
            (entity_id, field, reference_period, context.information_cutoff_utc.isoformat()),
        ).fetchall()
        return tuple(observation_from_row(row) for row in rows)
