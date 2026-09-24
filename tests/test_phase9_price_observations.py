from datetime import datetime, timedelta, timezone
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.data.immutable import ImmutableDatasetStore, ProviderDatasetAdapter
from asset_management.data.phase9 import Phase9Collector, ProviderBatch
from asset_management.data.phase9_price_observations import Phase9PriceObservationIngestor
from asset_management.domain.errors import ConfigurationError, DataQualityError, TemporalViolation
from asset_management.reference.instruments import InstrumentRepository
from asset_management.reference.corporate_actions import CorporateActionRepository
from asset_management.time.asof import AsOfContext
from asset_management.time.clock import FrozenClock
from asset_management.data.asof_query import AsOfRepository
from alpha_management.datafields import PointInTimeDataSource


ROOT = __import__("pathlib").Path(__file__).parents[1]
RECEIVED = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
EVENT = datetime(2026, 9, 4, 20, tzinfo=timezone.utc)
LICENSE = "purpose=internal-research;redistribution=forbidden;retention=perpetual"


def _batch(dataset, rows, *, revision="provider-r1", available=RECEIVED):
    return ProviderBatch(
        source="tiingo-eod", dataset=dataset, endpoint=f"/v1/{dataset}", http_method="GET",
        request={"dataset": dataset}, status_code=200, body={"result": rows},
        provider_timestamp=RECEIVED - timedelta(seconds=2), received_at=RECEIVED,
        available_at=available, source_revision=revision, schema_version=f"{dataset}-v1",
        license_tag=LICENSE, code_revision="git:abcdef0",
    )


def _admitted_snapshot(tmp_path, *, session_available=RECEIVED, instrument_updates=()):
    datasets = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    collector = Phase9Collector(ProviderDatasetAdapter(datasets))
    conn = sqlite3.connect(":memory:")
    Migrator(conn, FrozenClock(RECEIVED)).migrate(load_migration_catalog(ROOT / "schemas"))
    instrument_id = InstrumentRepository(conn).register(
        ticker="SPY", toss_symbol="SPY", vendor_symbol="SPY", cik=None,
        mic="XNYS", asset_class="ETF", currency="USD", timezone="America/New_York",
        effective_from=RECEIVED - timedelta(days=400), available_at=RECEIVED - timedelta(days=400),
        source="reference:fixture",
    )
    for update in instrument_updates:
        InstrumentRepository(conn).register(
            instrument_id=instrument_id, ticker=update.get("ticker", "SPY"),
            toss_symbol="SPY", vendor_symbol="SPY", cik=None,
            mic=update.get("mic", "XNYS"), asset_class="ETF",
            currency=update.get("currency", "USD"), timezone=update.get("timezone", "America/New_York"),
            effective_from=update.get("effective_from", EVENT + timedelta(days=1)),
            available_at=update.get("available_at", RECEIVED),
            effective_to=update.get("effective_to"), source="reference:fixture-update",
        )
    stamp = EVENT.isoformat()
    session = {
        "provider_entity_id": "XNYS", "exchange_local_date": "2026-09-04", "is_open": True,
        "regular_open_at": (EVENT - timedelta(hours=6, minutes=30)).isoformat(),
        "regular_close_at": EVENT.isoformat(), "event_time_utc": stamp,
        "received_at": RECEIVED.isoformat(), "available_at": RECEIVED.isoformat(),
        "source": "tiingo-eod", "source_revision": "provider-r1",
    }
    sessions = collector.trading_sessions(
        _batch("sessions", [session], available=session_available), {"XNYS": "XNYS"},
    )
    actions = collector.corporate_actions(_batch("actions", []), {"SPY": instrument_id})
    price = {
        "provider_instrument_id": "SPY", "event_time_utc": stamp,
        "available_at": RECEIVED.isoformat(), "exchange_local_date": "2026-09-04",
        "open": "100", "high": "102", "low": "99", "close": "101",
        "volume": "1000000", "currency": "USD", "session": "REGULAR",
        "adjustment": "total_return", "source": "tiingo-eod", "source_revision": "provider-r1",
    }
    prices = collector.daily_prices(_batch("daily-prices", [price]), {"SPY": instrument_id})
    gold = collector.attach_price_context(
        price_manifest_id=prices.silver_manifest_id,
        session_manifest_id=sessions.silver_manifest_id,
        action_manifest_id=actions.silver_manifest_id,
        instrument_exchange={instrument_id: "XNYS"}, code_revision="git:abcdef0",
    )
    return datasets, conn, prices.silver_manifest_id, gold.manifest_id, instrument_id


def test_phase9_silver_is_admitted_only_through_context_gold_and_is_pit_linked(tmp_path):
    datasets, conn, price_manifest, context_manifest, instrument_id = _admitted_snapshot(tmp_path)
    imported_at = RECEIVED + timedelta(minutes=1)
    ingestor = Phase9PriceObservationIngestor(datasets, conn)
    observation_ids = ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest, ingested_at=imported_at,
    )
    assert len(observation_ids) == 1
    assert ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest, ingested_at=imported_at,
    ) == observation_ids
    observation = AsOfRepository(conn).get_by_id(observation_ids[0])
    assert observation.entity_id == instrument_id
    assert observation.field == "price:total_return"
    assert observation.value == "101"
    assert observation.dataset_manifest_id == price_manifest
    assert observation.available_at == imported_at
    assert conn.execute(
        "SELECT context_manifest_id FROM am_price_observation_context WHERE observation_id=?",
        observation_ids,
    ).fetchone() == (context_manifest,)
    assert conn.execute(
        "SELECT COUNT(*) FROM am_manifest_parent WHERE child_manifest_id=?", (context_manifest,),
    ).fetchone()[0] == 3
    assert ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest,
        ingested_at=imported_at + timedelta(days=1),
    ) == observation_ids
    assert AsOfRepository(conn).get_by_id(observation_ids[0]).ingested_at == imported_at


def test_canonical_price_read_requires_exact_gold_admission(tmp_path):
    datasets, conn, price_manifest, context_manifest, instrument_id = _admitted_snapshot(tmp_path)
    ingestor = Phase9PriceObservationIngestor(datasets, conn)
    observation_id = ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest,
        ingested_at=RECEIVED + timedelta(minutes=1),
    )[0]
    context = AsOfContext(
        run_id="price-read", as_of_utc=RECEIVED + timedelta(minutes=2),
        information_cutoff_utc=RECEIVED + timedelta(minutes=2),
        policy_version="test", parameter_set_id="test", code_revision="test",
    )

    class Universe:
        def versions(self, kind, context):
            return {instrument_id: object()}

    source = PointInTimeDataSource(AsOfRepository(conn), datasets, Universe(),
                                   "tiingo-eod", "daily-prices")
    assert source.observation_evidence(
        "total_return_index", instrument_id=instrument_id, context=context,
        dataset_manifest_id=price_manifest,
    )[0].observation_id == observation_id
    assert source.time_series_observations(
        "total_return_index", instrument_id=instrument_id, context=context,
        dataset_manifest_id=price_manifest,
    ) == [("2026-09-04", "101")]
    conn.execute("DROP TRIGGER am_price_observation_context_delete_block")
    conn.execute("DELETE FROM am_price_observation_context WHERE observation_id=?", (observation_id,))
    with pytest.raises(DataQualityError, match="ALPHA_PRICE_CONTEXT_ADMISSION_MISSING"):
        source.observation_evidence("total_return_index", instrument_id=instrument_id,
                                    context=context, dataset_manifest_id=price_manifest)
    with pytest.raises(DataQualityError, match="ALPHA_PRICE_CONTEXT_ADMISSION_MISSING"):
        source.time_series_observations("total_return_index", instrument_id=instrument_id,
                                        context=context, dataset_manifest_id=price_manifest)


@pytest.mark.parametrize("field,value,error", [
    ("open", "not-decimal", "OPEN_NOT_DECIMAL_STRING"),
    ("high", "100", "OHLC_CONFLICT"),
    ("volume", "-1", "PRICE_SESSION_OR_VOLUME_INVALID"),
    ("source_revision", "", "SOURCE_REVISION_MISSING"),
    ("source_revision", "provider-r2", "PRICE_SILVER_BRONZE_MISMATCH"),
])
def test_direct_silver_write_cannot_skip_price_semantics(tmp_path, field, value, error):
    datasets, conn, price_manifest, context_manifest, instrument_id = _admitted_snapshot(tmp_path)
    manifest, rows = datasets.read(price_manifest)
    _, gold_body = datasets.read(context_manifest)
    malformed = dict(rows[0], **{field: value})
    silver = datasets.write(
        [malformed], layer="silver", source=manifest.source, dataset=manifest.dataset,
        schema_version=manifest.schema_version,
        retrieved_at=datetime.fromisoformat(manifest.retrieved_at),
        available_at=datetime.fromisoformat(manifest.available_at),
        provider_timestamp=datetime.fromisoformat(manifest.provider_timestamp),
        license_tag=manifest.license_tag, code_revision=manifest.code_revision,
        request_hash=manifest.request_hash, parent_manifest_ids=manifest.parent_manifest_ids,
    )
    gold = Phase9Collector(ProviderDatasetAdapter(datasets)).attach_price_context(
        price_manifest_id=silver.manifest_id,
        session_manifest_id=gold_body["session_manifest_id"],
        action_manifest_id=gold_body["action_manifest_id"],
        instrument_exchange={instrument_id: "XNYS"}, code_revision="git:abcdef0",
    )
    with pytest.raises(DataQualityError, match=error):
        Phase9PriceObservationIngestor(datasets, conn).ingest_total_return_silver(
            manifest_id=silver.manifest_id, context_manifest_id=gold.manifest_id,
            ingested_at=RECEIVED + timedelta(minutes=1),
        )
    assert conn.execute("SELECT COUNT(*) FROM am_temporal_observation").fetchone()[0] == 0


def test_price_import_replays_with_sqlite_row_factory(tmp_path):
    datasets, conn, price_manifest, context_manifest, _ = _admitted_snapshot(tmp_path)
    conn.row_factory = sqlite3.Row
    ingestor = Phase9PriceObservationIngestor(datasets, conn)
    first = ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest,
        ingested_at=RECEIVED + timedelta(minutes=1),
    )
    assert ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest,
        ingested_at=RECEIVED + timedelta(days=1),
    ) == first


def test_phase9_import_rejects_uncontextualized_silver(tmp_path):
    datasets, conn, price_manifest, _context_manifest, _instrument_id = _admitted_snapshot(tmp_path)
    with pytest.raises(DataQualityError, match="PRICE_CONTEXT_MANIFEST_INVALID"):
        Phase9PriceObservationIngestor(datasets, conn).ingest_total_return_silver(
            manifest_id=price_manifest,
            context_manifest_id=price_manifest,
            ingested_at=RECEIVED + timedelta(minutes=1),
        )


def test_price_availability_cannot_precede_late_context_gold(tmp_path):
    late_context = RECEIVED + timedelta(minutes=20)
    datasets, conn, price_manifest, context_manifest, _instrument_id = _admitted_snapshot(
        tmp_path, session_available=late_context,
    )
    assert datasets.read(context_manifest)[0].available_at == late_context.isoformat()
    with pytest.raises(TemporalViolation, match="available"):
        Phase9PriceObservationIngestor(datasets, conn).ingest_total_return_silver(
            manifest_id=price_manifest, context_manifest_id=context_manifest,
            ingested_at=RECEIVED + timedelta(minutes=10),
        )


def test_import_uses_instrument_version_effective_at_price_event(tmp_path):
    datasets, conn, price_manifest, context_manifest, instrument_id = _admitted_snapshot(
        tmp_path, instrument_updates=({"mic": "XPAR", "currency": "EUR",
                                      "timezone": "Europe/Paris"},),
    )
    ingestor = Phase9PriceObservationIngestor(datasets, conn)
    imported_at = RECEIVED + timedelta(days=2)
    ids = ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest,
        ingested_at=imported_at,
    )
    assert len(ids) == 1
    observation = AsOfRepository(conn).get_by_id(ids[0])
    assert observation.entity_id == instrument_id
    assert observation.source_timezone == "America/New_York"
    assert observation.ingested_at == imported_at
    assert ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest,
        ingested_at=imported_at + timedelta(days=1),
    ) == ids
    assert AsOfRepository(conn).get_by_id(ids[0]).ingested_at == imported_at


def test_pre_delisting_price_can_be_imported_after_delisting(tmp_path):
    datasets, conn, price_manifest, context_manifest, instrument_id = _admitted_snapshot(tmp_path)
    CorporateActionRepository(conn).record(
        action_id="delist-spy", instrument_id=instrument_id,
        action_type="DELISTING", terms={"reason": "fixture"},
        effective_from=EVENT + timedelta(days=1), available_at=RECEIVED,
        source="reference:fixture",
    )
    imported_at = RECEIVED + timedelta(days=2)
    ingestor = Phase9PriceObservationIngestor(datasets, conn)
    ids = ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest,
        ingested_at=imported_at,
    )
    assert AsOfRepository(conn).get_by_id(ids[0]).source_timezone == "America/New_York"
    assert ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=context_manifest,
        ingested_at=imported_at + timedelta(days=1),
    ) == ids


def test_new_gold_context_for_same_silver_creates_a_bound_revision(tmp_path):
    datasets, conn, price_manifest, first_context, instrument_id = _admitted_snapshot(tmp_path)
    ingestor = Phase9PriceObservationIngestor(datasets, conn)
    first = ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=first_context,
        ingested_at=RECEIVED + timedelta(minutes=1),
    )[0]
    first_body = datasets.read(first_context)[1]
    collector = Phase9Collector(ProviderDatasetAdapter(datasets))
    newer_actions = collector.corporate_actions(
        _batch("actions", [], revision="provider-r2",
               available=RECEIVED + timedelta(minutes=2)),
        {"SPY": instrument_id},
    )
    second_context = collector.attach_price_context(
        price_manifest_id=price_manifest,
        session_manifest_id=first_body["session_manifest_id"],
        action_manifest_id=newer_actions.silver_manifest_id,
        instrument_exchange={instrument_id: "XNYS"}, code_revision="git:abcdef0",
    ).manifest_id
    second = ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=second_context,
        ingested_at=RECEIVED + timedelta(minutes=3),
    )[0]
    assert second != first
    assert AsOfRepository(conn).get_by_id(second).supersedes_observation_id == first
    assert conn.execute(
        "SELECT observation_id, context_manifest_id FROM am_price_observation_context "
        "ORDER BY observation_id",
    ).fetchall() == sorted([(first, first_context), (second, second_context)])
    assert ingestor.ingest_total_return_silver(
        manifest_id=price_manifest, context_manifest_id=second_context,
        ingested_at=RECEIVED + timedelta(days=1),
    ) == (second,)


def test_future_price_cannot_enter_current_pit_cutoff(tmp_path):
    datasets, conn, original_price, original_context, instrument_id = _admitted_snapshot(tmp_path)
    original_row = datasets.read(original_price)[1][0]
    original_body = datasets.read(original_context)[1]
    future_event = EVENT + timedelta(days=10)
    future_date = future_event.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    future_price = dict(original_row, event_time_utc=future_event.isoformat(),
                        exchange_local_date=future_date,
                        available_at=(future_event + timedelta(hours=1)).isoformat())
    future_session = dict(datasets.read(original_body["session_manifest_id"])[1][0],
                          exchange_local_date=future_date,
                          regular_open_at=(future_event - timedelta(hours=6)).isoformat(),
                          regular_close_at=future_event.isoformat(),
                          event_time_utc=future_event.isoformat())
    collector = Phase9Collector(ProviderDatasetAdapter(datasets))
    price = collector.daily_prices(
        _batch("daily-prices", [
            {key: value for key, value in future_price.items() if key != "instrument_id"}
        ], available=future_event + timedelta(hours=1)),
        {"SPY": instrument_id},
    )
    session = collector.trading_sessions(
        _batch("sessions", [
            {key: value for key, value in future_session.items() if key != "entity_id"}
        ], available=future_event + timedelta(hours=1)),
        {"XNYS": "XNYS"},
    )
    context = collector.attach_price_context(
        price_manifest_id=price.silver_manifest_id,
        session_manifest_id=session.silver_manifest_id,
        action_manifest_id=original_body["action_manifest_id"],
        instrument_exchange={instrument_id: "XNYS"}, code_revision="git:abcdef0",
    )
    with pytest.raises(DataQualityError, match="PRICE_EVENT_TIME_ORDER_INVALID"):
        Phase9PriceObservationIngestor(datasets, conn).ingest_total_return_silver(
            manifest_id=price.silver_manifest_id, context_manifest_id=context.manifest_id,
            ingested_at=RECEIVED + timedelta(minutes=1),
        )
    assert conn.execute("SELECT COUNT(*) FROM am_temporal_observation").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [
    ("regular_open_at", "not-a-time"),
    ("regular_close_at", "not-a-time"),
    ("event_time_utc", "not-a-time"),
])
def test_import_rejects_malformed_session_semantics(tmp_path, field, value):
    datasets, conn, price_manifest, context_manifest_id, _ = _admitted_snapshot(tmp_path)
    _context_manifest, context_body = datasets.read(context_manifest_id)
    session_manifest_id = context_body["session_manifest_id"]
    session_manifest, session_rows = datasets.read(session_manifest_id)
    session_rows[0][field] = value
    invalid_sessions = datasets.write(
        session_rows, layer="silver", source="tiingo-eod", dataset="sessions",
        schema_version="sessions-test", retrieved_at=RECEIVED, available_at=RECEIVED,
        provider_timestamp=RECEIVED - timedelta(seconds=2), license_tag=LICENSE,
        code_revision="git:abcdef0", request_hash="e" * 64,
        parent_manifest_ids=session_manifest.parent_manifest_ids,
    )
    action_manifest, actions = datasets.read(context_body["action_manifest_id"])
    forged_context = datasets.write(
        {"status": "VALID", "price_manifest_id": price_manifest,
         "session_manifest_id": invalid_sessions.manifest_id,
         "action_manifest_id": action_manifest.manifest_id,
         "price_row_count": len(datasets.read(price_manifest)[1]),
         "action_row_count": len(actions)},
        layer="gold", source="tiingo-eod", dataset="daily-prices-with-context",
        schema_version="phase9-price-context-v1", retrieved_at=RECEIVED,
        available_at=RECEIVED, provider_timestamp=RECEIVED - timedelta(seconds=2),
        license_tag=LICENSE, code_revision="git:abcdef0", request_hash="f" * 64,
        parent_manifest_ids=(price_manifest, invalid_sessions.manifest_id, action_manifest.manifest_id),
    )
    with pytest.raises(DataQualityError, match="PRICE_SESSION_SILVER_SEMANTICS_INVALID"):
        Phase9PriceObservationIngestor(datasets, conn).ingest_total_return_silver(
            manifest_id=price_manifest, context_manifest_id=forged_context.manifest_id,
            ingested_at=RECEIVED + timedelta(minutes=1),
        )


def test_context_gold_cannot_substitute_unrelated_session_dataset(tmp_path):
    datasets, conn, price_manifest, context_manifest_id, _instrument_id = _admitted_snapshot(tmp_path)
    _context, context_body = datasets.read(context_manifest_id)
    session_id = str(context_body["session_manifest_id"])
    session_manifest, sessions = datasets.read(session_id)
    action_id = str(context_body["action_manifest_id"])
    action_manifest, actions = datasets.read(action_id)
    unrelated_sessions = datasets.write(
        sessions, layer="silver", source="tiingo-eod", dataset="macro",
        schema_version="macro-v1", retrieved_at=RECEIVED, available_at=RECEIVED,
        provider_timestamp=RECEIVED - timedelta(seconds=2), license_tag=LICENSE,
        code_revision="git:abcdef0", request_hash="c" * 64,
        parent_manifest_ids=session_manifest.parent_manifest_ids,
    )
    forged_context = datasets.write(
        {
            "status": "VALID", "price_manifest_id": price_manifest,
            "session_manifest_id": unrelated_sessions.manifest_id,
            "action_manifest_id": action_id, "price_row_count": len(datasets.read(price_manifest)[1]),
            "action_row_count": len(actions),
        },
        layer="gold", source="tiingo-eod", dataset="daily-prices-with-context",
        schema_version="phase9-price-context-v1", retrieved_at=RECEIVED,
        available_at=RECEIVED, provider_timestamp=RECEIVED - timedelta(seconds=2),
        license_tag=LICENSE, code_revision="git:abcdef0", request_hash="d" * 64,
        parent_manifest_ids=(price_manifest, unrelated_sessions.manifest_id, action_manifest.manifest_id),
    )
    with pytest.raises(DataQualityError, match="PRICE_CONTEXT_PARENT_INVALID"):
        Phase9PriceObservationIngestor(datasets, conn).ingest_total_return_silver(
            manifest_id=price_manifest, context_manifest_id=forged_context.manifest_id,
            ingested_at=RECEIVED + timedelta(minutes=1),
        )


def test_price_import_refuses_a_caller_owned_transaction(tmp_path):
    datasets, conn, price_manifest, context_manifest, _instrument_id = _admitted_snapshot(tmp_path)
    conn.execute("BEGIN")
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("caller-run", RECEIVED.isoformat(), RECEIVED.isoformat(), "git:abcdef0", RECEIVED.isoformat()))
    with pytest.raises(DataQualityError, match="PRICE_IMPORT_TRANSACTION_ALREADY_ACTIVE"):
        Phase9PriceObservationIngestor(datasets, conn).ingest_total_return_silver(
            manifest_id=price_manifest, context_manifest_id=context_manifest,
            ingested_at=RECEIVED + timedelta(minutes=1),
        )
    assert conn.in_transaction
    assert conn.execute("SELECT 1 FROM am_runtime_run WHERE runtime_run_id='caller-run'").fetchone()
    conn.rollback()


def test_phase9_import_rejects_non_total_return_prices(tmp_path):
    datasets, conn, price_manifest, context_manifest, _instrument_id = _admitted_snapshot(tmp_path)
    _manifest, rows = datasets.read(price_manifest)
    rows[0]["adjustment"] = "raw"
    bad = datasets.write(
        rows, layer="silver", source="tiingo-eod", dataset="daily-prices",
        schema_version="daily-prices-test", retrieved_at=RECEIVED,
        available_at=RECEIVED, provider_timestamp=RECEIVED - timedelta(seconds=2),
        license_tag=LICENSE, code_revision="git:abcdef0", request_hash="a" * 64,
        parent_manifest_ids=(datasets.read(price_manifest)[0].parent_manifest_ids[0],),
    )
    # The pinned Gold receipt cannot be reused to bless a different Silver artifact.
    with pytest.raises(DataQualityError, match="PRICE_CONTEXT_MANIFEST_MISMATCH"):
        Phase9PriceObservationIngestor(datasets, conn).ingest_total_return_silver(
            manifest_id=bad.manifest_id, context_manifest_id=context_manifest,
            ingested_at=RECEIVED + timedelta(minutes=1),
        )


def test_manifest_registry_preserves_distinct_ids_for_identical_content():
    conn = sqlite3.connect(":memory:")
    migrations = load_migration_catalog(ROOT / "schemas")
    migrator = Migrator(conn, FrozenClock(RECEIVED))
    migrator.migrate(item for item in migrations if item.version <= 20)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("runtime", RECEIVED.isoformat(), RECEIVED.isoformat(), "git:abcdef0", RECEIVED.isoformat()))
    conn.execute("INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
                 ("ingestion", "runtime", "tiingo-eod", RECEIVED.isoformat(), RECEIVED.isoformat()))
    parent = ("ingestion", "bronze", "daily-prices-raw", "immutable://parent", "raw-content",
              RECEIVED.isoformat(), RECEIVED.isoformat(), "raw-v1", 1)
    first = ("ingestion", "silver", "daily-prices", "immutable://content", "same-content",
             RECEIVED.isoformat(), RECEIVED.isoformat(), "schema-v1", 1)
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 ("manifest-parent", *parent))
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 ("manifest-a", *first))
    conn.execute("INSERT INTO am_manifest_parent VALUES (?, ?)", ("manifest-a", "manifest-parent"))
    timestamp = RECEIVED.isoformat()
    conn.execute(
        """INSERT INTO am_temporal_observation VALUES (
             ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
           )""",
        ("observation-a", "instrument", "price:total_return", '"101"', "2026-09-05",
         timestamp, None, None, timestamp, timestamp, timestamp, timestamp, None,
         "America/New_York", "schema-v1", None, "manifest-a", None, "observation-hash"),
    )
    migrator.migrate(migrations)
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 ("manifest-b", *first))
    assert conn.execute("SELECT parent_manifest_id FROM am_manifest_parent WHERE child_manifest_id='manifest-a'").fetchone() == ("manifest-parent",)
    assert conn.execute(
        "SELECT manifest.dataset_manifest_id FROM am_temporal_observation observation "
        "JOIN am_dataset_manifest manifest ON manifest.dataset_manifest_id=observation.dataset_manifest_id "
        "WHERE observation_id='observation-a'",
    ).fetchone() == ("manifest-a",)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE am_dataset_manifest SET uri='changed' WHERE dataset_manifest_id='manifest-a'")


def test_manifest_identity_migration_rolls_back_when_legacy_fk_check_fails():
    conn = sqlite3.connect(":memory:")
    migrations = load_migration_catalog(ROOT / "schemas")
    migrator = Migrator(conn, FrozenClock(RECEIVED))
    migrator.migrate(item for item in migrations if item.version <= 20)
    conn.execute("INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                 ("runtime", RECEIVED.isoformat(), RECEIVED.isoformat(), "git:abcdef0", RECEIVED.isoformat()))
    conn.execute("INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
                 ("ingestion", "runtime", "tiingo-eod", RECEIVED.isoformat(), RECEIVED.isoformat()))
    first = ("ingestion", "silver", "daily-prices", "immutable://content", "same-content",
             RECEIVED.isoformat(), RECEIVED.isoformat(), "schema-v1", 1)
    conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 ("manifest-a", *first))
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO am_manifest_parent VALUES (?, ?)", ("manifest-a", "orphan-parent"))
    conn.execute("PRAGMA foreign_keys=ON")

    with pytest.raises(ConfigurationError, match="invalid foreign keys"):
        migrator.migrate(migrations)
    assert conn.execute("SELECT 1 FROM schema_migration WHERE version=21").fetchone() is None
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='am_dataset_manifest_legacy'").fetchone() is None
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        conn.execute("INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("manifest-b", *first))
