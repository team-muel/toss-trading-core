from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.data.asof_query import AsOfRepository
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.data.repositories import SQLiteTemporalObservationStore
from asset_management.domain.errors import DataQualityError, InvariantViolation, TemporalViolation
from asset_management.time.asof import AsOfContext, require_as_of_context
from asset_management.time.clock import FrozenClock
from asset_management.time.timezone import utc


ROOT = Path(__file__).parents[1]
UTC = timezone.utc


def at(month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def pit():
    conn = sqlite3.connect(":memory:")
    Migrator(conn, FrozenClock(at(9, 4))).migrate(load_migration_catalog(ROOT / "schemas"))
    raw = SQLiteRawResponseStore(conn)
    source_ids: dict[str, str] = {}

    def evidence(name: str, received: datetime) -> str:
        identifier = raw.append(
            source="test-provider", endpoint=f"/{name}", http_method="GET",
            request_payload={"name": name}, status_code=200, body={"name": name},
            requested_at=received, received_at=received, account_id=None,
            schema_version="pit-v1",
        )
        source_ids[name] = identifier
        return identifier

    yield conn, SQLiteTemporalObservationStore(conn), AsOfRepository(conn), evidence
    conn.close()


def context(cutoff: datetime) -> AsOfContext:
    return AsOfContext("run", cutoff, cutoff, "temporal-v1", "params", "sha")


def append_value(store, evidence, *, name: str, value: object, event: datetime,
                 received: datetime, available: datetime | None = None,
                 reference_period: str = "2026-01-01", official: datetime | None = None,
                 revised: datetime | None = None, supersedes: str | None = None):
    raw_id = evidence(name, received)
    return store.append(
        entity_id="SERIES", field="value", value=value,
        reference_period=reference_period, event_time=event,
        scheduled_release_at=official, official_release_at=official,
        source_timestamp=official or event, received_at=received,
        ingested_at=received, available_at=available or received,
        revised_at=revised, source_timezone="UTC", schema_version="pit-v1",
        raw_response_id=raw_id, dataset_manifest_id=None,
        supersedes_observation_id=supersedes,
    )


def test_future_sentinel_and_same_asof_are_deterministic(pit):
    _, store, repository, evidence = pit
    append_value(store, evidence, name="known", value="10", event=at(1, 1), received=at(1, 2))
    append_value(
        store, evidence, name="future", value="999", event=at(1, 3),
        received=at(1, 4), reference_period="2026-01-03",
    )
    first = repository.get_latest(entity_id="SERIES", field="value", context=context(at(1, 3)))
    second = repository.get_latest(entity_id="SERIES", field="value", context=context(at(1, 3)))
    assert first.value == "10"
    assert first == second


def test_same_day_close_is_not_visible_to_morning_decision(pit):
    _, store, repository, evidence = pit
    close = at(3, 9, 20)  # 16:00 America/New_York after the DST transition.
    append_value(
        store, evidence, name="close", value="101.25", event=close,
        received=at(3, 9, 20, 1), available=at(3, 9, 20, 1),
        reference_period="2026-03-09T16:00:00-04:00", official=close,
    )
    with pytest.raises(DataQualityError, match="MISSING"):
        repository.get_latest(
            entity_id="SERIES", field="value", context=context(at(3, 9, 14))
        )


def test_revision_vintage_replays_original_before_revision_release(pit):
    _, store, repository, evidence = pit
    original = append_value(
        store, evidence, name="q2-original", value="2.1", event=at(6, 30),
        received=at(7, 30, 12, 31), available=at(7, 30, 12, 31),
        reference_period="2026-Q2", official=at(7, 30, 12, 30),
    )
    revision = append_value(
        store, evidence, name="q2-revision", value="1.8", event=at(6, 30),
        received=at(9, 1, 12, 31), available=at(9, 1, 12, 31),
        reference_period="2026-Q2", official=at(9, 1, 12, 30),
        revised=at(9, 1, 12, 30), supersedes=original.observation_id,
    )
    assert repository.get_vintage(
        entity_id="SERIES", field="value", reference_period="2026-Q2",
        context=context(at(8, 1)),
    ).observation_id == original.observation_id
    assert repository.get_vintage(
        entity_id="SERIES", field="value", reference_period="2026-Q2",
        context=context(at(9, 2)),
    ).observation_id == revision.observation_id
    assert repository.history(
        entity_id="SERIES", field="value", reference_period="2026-Q2",
        context=context(at(9, 2)),
    ) == (original, revision)
    assert repository.history(
        entity_id="SERIES", field="value", reference_period="2026-Q2",
        context=context(at(8, 1)),
    ) == (original,)


def test_delayed_receipt_and_ingestion_define_actual_availability(pit):
    _, store, repository, evidence = pit
    append_value(
        store, evidence, name="delayed", value="3.0", event=at(8, 1),
        official=at(8, 2, 12, 30), received=at(8, 2, 12, 35),
        available=at(8, 2, 12, 35), reference_period="2026-07",
    )
    with pytest.raises(DataQualityError, match="MISSING"):
        repository.get_latest(
            entity_id="SERIES", field="value", context=context(at(8, 2, 12, 34))
        )
    assert repository.get_latest(
        entity_id="SERIES", field="value", context=context(at(8, 2, 12, 35))
    ).value == "3.0"


def test_dst_conversion_and_timezone_naive_inputs():
    eastern = ZoneInfo("America/New_York")
    assert utc(datetime(2026, 3, 6, 16, tzinfo=eastern)) == at(3, 6, 21)
    assert utc(datetime(2026, 3, 9, 16, tzinfo=eastern)) == at(3, 9, 20)
    with pytest.raises(TemporalViolation, match="naive"):
        utc(datetime(2026, 3, 9, 16))
    with pytest.raises(TemporalViolation, match="required"):
        require_as_of_context(None)  # type: ignore[arg-type]


def test_invalid_availability_float_lineage_and_mutation_fail_closed(pit):
    conn, store, _, evidence = pit
    raw_id = evidence("invalid", at(2, 2, 12, 35))
    base = dict(
        entity_id="SERIES", field="value", value="1.0", reference_period="2026-01",
        event_time=at(2, 1), scheduled_release_at=at(2, 2, 12, 30),
        official_release_at=at(2, 2, 12, 30), source_timestamp=at(2, 2, 12, 30),
        received_at=at(2, 2, 12, 35), ingested_at=at(2, 2, 12, 35),
        available_at=at(2, 2, 12, 34), revised_at=None, source_timezone="UTC",
        schema_version="pit-v1", raw_response_id=raw_id, dataset_manifest_id=None,
        supersedes_observation_id=None,
    )
    with pytest.raises(TemporalViolation, match="available_at"):
        store.append(**base)
    base["available_at"] = at(2, 2, 12, 35)
    base["value"] = 1.0
    with pytest.raises(TypeError, match="float"):
        store.append(**base)
    base["value"] = "1.0"
    base["raw_response_id"] = "missing"
    with pytest.raises(InvariantViolation, match="FOREIGN KEY"):
        store.append(**base)
    base["raw_response_id"] = raw_id
    observation = store.append(**base)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE am_temporal_observation SET value_json = '\"2.0\"' WHERE observation_id = ?",
            (observation.observation_id,),
        )


def test_revision_cannot_cross_series_or_branch(pit):
    _, store, _, evidence = pit
    original = append_value(
        store, evidence, name="base", value="1", event=at(1, 1), received=at(1, 2)
    )
    append_value(
        store, evidence, name="revision-1", value="2", event=at(1, 1),
        received=at(1, 3), revised=at(1, 3), supersedes=original.observation_id,
    )
    with pytest.raises(InvariantViolation, match="cannot branch"):
        append_value(
            store, evidence, name="revision-2", value="3", event=at(1, 1),
            received=at(1, 4), revised=at(1, 4), supersedes=original.observation_id,
        )
    with pytest.raises(InvariantViolation, match="explicitly superseded"):
        append_value(
            store, evidence, name="unlinked", value="4", event=at(1, 1),
            received=at(1, 5),
        )


def test_database_rejects_non_utc_text_even_when_application_is_bypassed(pit):
    conn, _, _, evidence = pit
    raw_id = evidence("direct", at(1, 2))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO am_temporal_observation VALUES (
              'bad', 'SERIES', 'value', '\"1\"', '2026-01',
              'not-a-time', NULL, NULL, '2026-01-01T00:00:00+00:00',
              '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00',
              '2026-01-02T00:00:00+00:00', NULL, 'UTC', 'pit-v1', ?, NULL, NULL,
              'bad-hash'
            )
            """,
            (raw_id,),
        )


def test_database_rejects_timezone_naive_account_truth(pit):
    conn, _, _, evidence = pit
    raw_id = evidence("account", at(1, 2))
    conn.execute(
        "INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
        ("account-run", at(1, 2).isoformat(), at(1, 2).isoformat(), "sha",
         at(1, 2).isoformat()),
    )
    with pytest.raises(sqlite3.IntegrityError, match="chronological UTC"):
        conn.execute(
            """INSERT INTO am_account_snapshot VALUES
               ('bad-account','account-run','account-1','2026-01-02T00:00:00',?,
                '{}','hash')""",
            (raw_id,),
        )
