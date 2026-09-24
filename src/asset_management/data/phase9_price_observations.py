"""Admit immutable Phase 9 Tiingo Silver prices to the canonical PIT store."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import sqlite3
from zoneinfo import ZoneInfo

from asset_management.data.immutable import ImmutableDatasetStore, StoredDatasetManifest, canonical
from asset_management.data.phase9 import ACTION_FIELDS, PRICE_FIELDS, SESSION_FIELDS, normalize_prices
from asset_management.data.asof_query import AsOfRepository
from asset_management.data.prices import PriceBasis, PriceObservationStore
from asset_management.domain.errors import DataQualityError
from asset_management.reference.instruments import InstrumentRepository
from asset_management.time.asof import AsOfContext
from asset_management.time.timezone import utc


def _instant(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DataQualityError(f"{field.upper()}_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataQualityError(f"{field.upper()}_NOT_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


class Phase9PriceObservationIngestor:
    """Register and import one full Tiingo daily-price Silver snapshot.

    The immutable Silver manifest remains the source of truth. This adapter
    registers its complete bronze/silver lineage in the AMA-162 SQLite registry
    and writes only total-return observations linked to the Silver manifest.
    """

    def __init__(self, datasets: ImmutableDatasetStore, conn: sqlite3.Connection):
        self.datasets = datasets
        self.conn = conn
        self.instruments = InstrumentRepository(conn)
        self.prices = PriceObservationStore(conn)
        self.conn.execute("PRAGMA foreign_keys=ON")

    def ingest_total_return_silver(
        self, *, manifest_id: str, context_manifest_id: str, ingested_at: datetime,
    ) -> tuple[str, ...]:
        """Admit Tiingo prices only when their Phase 9 session/action gate is pinned."""
        if self.conn.in_transaction:
            raise DataQualityError("PRICE_IMPORT_TRANSACTION_ALREADY_ACTIVE")
        imported = utc(ingested_at)
        manifest, body = self.datasets.read(manifest_id)
        context_manifest, context_body = self.datasets.read(context_manifest_id)
        if (manifest.source, manifest.dataset, manifest.layer, manifest.quality_status) != (
            "tiingo-eod", "daily-prices", "silver", "VALID",
        ):
            raise DataQualityError("PRICE_SILVER_MANIFEST_CONTRACT_INVALID")
        if (context_manifest.dataset, context_manifest.layer, context_manifest.quality_status) != (
            "daily-prices-with-context", "gold", "VALID",
        ) or not isinstance(context_body, dict) or context_body.get("status") != "VALID":
            raise DataQualityError("PRICE_CONTEXT_MANIFEST_INVALID")
        if context_body.get("price_manifest_id") != manifest_id:
            raise DataQualityError("PRICE_CONTEXT_MANIFEST_MISMATCH")
        if set(context_manifest.parent_manifest_ids) != {
            context_body.get("price_manifest_id"), context_body.get("session_manifest_id"),
            context_body.get("action_manifest_id"),
        }:
            raise DataQualityError("PRICE_CONTEXT_LINEAGE_MISMATCH")
        session_manifest, sessions = self.datasets.read(str(context_body["session_manifest_id"]))
        action_manifest, actions = self.datasets.read(str(context_body["action_manifest_id"]))
        if (session_manifest.dataset, session_manifest.layer, session_manifest.quality_status) != (
            "sessions", "silver", "VALID",
        ) or (action_manifest.dataset, action_manifest.layer, action_manifest.quality_status) != (
            "actions", "silver", "VALID",
        ):
            raise DataQualityError("PRICE_CONTEXT_PARENT_INVALID")
        if not isinstance(sessions, list) or not isinstance(actions, list):
            raise DataQualityError("PRICE_CONTEXT_PARENT_ROWS_INVALID")
        self._validate_context_rows(sessions, {**SESSION_FIELDS, "entity_id": "string"},
                                    session_manifest, "SESSION")
        self._validate_context_rows(actions, {**ACTION_FIELDS, "instrument_id": "string"},
                                    action_manifest, "ACTION")
        if imported < _instant(manifest.retrieved_at, "retrieved_at"):
            raise DataQualityError("PRICE_IMPORT_PRECEDES_RECEIPT")
        if not isinstance(body, list) or not body:
            raise DataQualityError("PRICE_SILVER_ROWS_REQUIRED")
        if len(body) != manifest.row_count:
            raise DataQualityError("PRICE_SILVER_ROW_COUNT_MISMATCH")
        for row in body:
            if not isinstance(row, dict):
                raise DataQualityError("PRICE_SILVER_ROW_INVALID")
            if not self._matches_schema(row, {**PRICE_FIELDS, "instrument_id": "string"}):
                raise DataQualityError("PRICE_SILVER_SCHEMA_INVALID")
        # The immutable store can be written directly. Reapply the producer's
        # semantic contract before assigning canonical PIT authority.
        normalize_prices({"result": body})
        if len(manifest.parent_manifest_ids) != 1:
            raise DataQualityError("PRICE_SILVER_BRONZE_LINEAGE_INVALID")
        bronze, raw = self.datasets.read(manifest.parent_manifest_ids[0])
        if (bronze.source, bronze.dataset, bronze.layer) != (
            manifest.source, manifest.dataset, "bronze",
        ):
            raise DataQualityError("PRICE_SILVER_BRONZE_LINEAGE_INVALID")
        normalized = normalize_prices(raw)
        silver_rows = [{key: value for key, value in row.items() if key != "instrument_id"}
                       for row in body]
        if sorted(map(canonical, silver_rows)) != sorted(map(canonical, normalized)):
            raise DataQualityError("PRICE_SILVER_BRONZE_MISMATCH")

        lineage_key = hashlib.sha256(
            f"{manifest.manifest_id}:{context_manifest.manifest_id}".encode()
        ).hexdigest()
        runtime_id = f"phase9-price-import:{lineage_key}"
        context = AsOfContext(
            run_id=runtime_id,
            as_of_utc=imported,
            information_cutoff_utc=imported,
            policy_version="phase9-price-import-v1",
            parameter_set_id=lineage_key,
            code_revision=context_manifest.code_revision,
        )
        session_keys = {
            (row.get("entity_id"), row.get("exchange_local_date"))
            for row in sessions if isinstance(row, dict) and row.get("is_open") is True
        }
        if (context_body.get("price_row_count") != len(body) or
                context_body.get("action_row_count") != len(actions)):
            raise DataQualityError("PRICE_CONTEXT_ROW_COUNT_MISMATCH")
        if any(not isinstance(row, dict) for row in sessions + actions):
            raise DataQualityError("PRICE_CONTEXT_PARENT_ROWS_INVALID")
        seen: set[tuple[str, str]] = set()
        observations = []
        for row in body:
            if row["source"] != manifest.source or row["adjustment"] != PriceBasis.TOTAL_RETURN.value:
                raise DataQualityError("PRICE_BASIS_OR_SOURCE_INVALID")
            if row["session"] != "REGULAR":
                raise DataQualityError("PRICE_SESSION_INVALID")
            try:
                reference_date = date.fromisoformat(row["exchange_local_date"])
            except ValueError as exc:
                raise DataQualityError("PRICE_REFERENCE_PERIOD_INVALID") from exc
            event_time = _instant(row["event_time_utc"], "event_time_utc")
            row_available = _instant(row["available_at"], "available_at")
            if (event_time > imported or
                    event_time > _instant(manifest.retrieved_at, "retrieved_at") or
                    row_available < event_time):
                raise DataQualityError("PRICE_EVENT_TIME_ORDER_INVALID")
            # Resolve canonical metadata at the market event instant. Import-time
            # metadata can describe a later listing, venue, currency, or timezone.
            instrument = self.instruments.effective("INSTRUMENT", event_time, context).get(
                row["instrument_id"]
            )
            if instrument is None:
                raise DataQualityError("INSTRUMENT_NOT_LISTED_AT_EVENT")
            event_context = AsOfContext(
                run_id=context.run_id, as_of_utc=event_time,
                information_cutoff_utc=min(event_time, context.information_cutoff_utc),
                policy_version=context.policy_version, parameter_set_id=context.parameter_set_id,
                code_revision=context.code_revision,
            )
            if any(action["instrument_id"] == row["instrument_id"] and
                   action["action_type"] == "DELISTING"
                   for action in self.instruments.active("ACTION", event_context).values()):
                raise DataQualityError("INSTRUMENT_DELISTED")
            if event_time.astimezone(ZoneInfo(instrument["timezone"])).date() != reference_date:
                raise DataQualityError("PRICE_EVENT_DATE_MISMATCH")
            if (instrument["mic"], reference_date.isoformat()) not in session_keys:
                raise DataQualityError("PRICE_SESSION_MISSING_OR_CLOSED")
            key = (row["instrument_id"], reference_date.isoformat())
            if key in seen:
                raise DataQualityError("PRICE_PERIOD_DUPLICATE")
            seen.add(key)
            if instrument["currency"] != row["currency"]:
                raise DataQualityError("PRICE_CURRENCY_MISMATCH")
            source_timezone = ZoneInfo(instrument["timezone"]).key
            available = max(
                row_available,
                _instant(manifest.available_at, "available_at"),
                _instant(context_manifest.available_at, "context_available_at"),
                imported,
            )
            context.require_known_at(available, label="price observation")
            observation_id = hashlib.sha256(
                f"{manifest.manifest_id}:{context_manifest.manifest_id}:"
                f"{row['instrument_id']}:{reference_date.isoformat()}".encode()
            ).hexdigest()
            existing = self.conn.execute(
                "SELECT observation_id FROM am_temporal_observation WHERE observation_id=?",
                (observation_id,),
            ).fetchone()
            if existing is not None:
                recorded = AsOfRepository(self.conn).get_by_id(observation_id)
                expected = (
                    row["instrument_id"], "price:total_return", row["close"],
                    reference_date.isoformat(), event_time,
                    _instant(manifest.provider_timestamp, "provider_timestamp"),
                    _instant(manifest.retrieved_at, "retrieved_at"),
                    source_timezone, manifest.schema_version,
                    manifest.manifest_id,
                )
                actual = (
                    recorded.entity_id, recorded.field, recorded.value,
                    recorded.reference_period, recorded.event_time,
                    recorded.source_timestamp, recorded.received_at,
                    recorded.source_timezone, recorded.schema_version,
                    recorded.dataset_manifest_id,
                )
                if actual != expected:
                    raise DataQualityError("PRICE_OBSERVATION_REPLAY_CONFLICT")
                admission = self.conn.execute(
                    "SELECT context_manifest_id FROM am_price_observation_context "
                    "WHERE observation_id=?", (observation_id,),
                ).fetchone()
                if admission is None or admission[0] != context_manifest.manifest_id:
                    raise DataQualityError("PRICE_CONTEXT_LINEAGE_MISSING_OR_CONFLICTING")
                context.require_known_at(recorded.available_at, label="price observation replay")
                observations.append((row, reference_date, event_time, source_timezone,
                                     recorded.available_at,
                                     observation_id, None, True))
                continue
            prior = self.conn.execute(
                """SELECT observation_id, available_at_utc, content_hash, dataset_manifest_id
                   FROM am_temporal_observation
                   WHERE entity_id=? AND field_name=? AND reference_period=?
                   ORDER BY available_at_utc DESC, observation_id DESC LIMIT 2""",
                (row["instrument_id"], "price:total_return", reference_date.isoformat()),
            ).fetchall()
            if len(prior) > 1 and prior[0][1] == prior[1][1] and prior[0][2] != prior[1][2]:
                raise DataQualityError("PRICE_VINTAGE_CONFLICT")
            if prior and prior[0][3] == manifest.manifest_id:
                admission = self.conn.execute(
                    "SELECT context_manifest_id FROM am_price_observation_context "
                    "WHERE observation_id=?", (prior[0][0],),
                ).fetchone()
                if admission is None:
                    raise DataQualityError("PRICE_PRIOR_CONTEXT_LINEAGE_MISSING")
            if prior and available <= _instant(prior[0][1], "prior_available_at"):
                raise DataQualityError("PRICE_CONTEXT_REVISION_NOT_LATER")
            supersedes = str(prior[0][0]) if prior else None
            observations.append((row, reference_date, event_time, source_timezone,
                                 available, observation_id, supersedes, False))

        # Validate and write the entire import in one transaction. The underlying
        # observation repository preserves an enclosing transaction.
        with self.conn:
            self._register_lineage(context_manifest, imported)
            inserted = []
            for row, period, event_time, source_timezone, available, observation_id, supersedes, reused in observations:
                if reused:
                    inserted.append(observation_id)
                    continue
                observation = self.prices.append(
                    instrument_id=row["instrument_id"], basis=PriceBasis.TOTAL_RETURN,
                    price=row["close"], context=context,
                    observation_id=observation_id, reference_period=period.isoformat(),
                    event_time=event_time, scheduled_release_at=None, official_release_at=None,
                    source_timestamp=_instant(manifest.provider_timestamp, "provider_timestamp"),
                    received_at=_instant(manifest.retrieved_at, "retrieved_at"),
                    available_at=available, ingested_at=imported,
                    revised_at=available if supersedes else None,
                    source_timezone=source_timezone,
                    schema_version=manifest.schema_version,
                    raw_response_id=None, dataset_manifest_id=manifest.manifest_id,
                    supersedes_observation_id=supersedes,
                )
                self.conn.execute(
                    "INSERT INTO am_price_observation_context VALUES (?, ?)",
                    (observation.observation_id, context_manifest.manifest_id),
                )
                inserted.append(observation.observation_id)
        return tuple(inserted)

    @staticmethod
    def _matches_schema(row: dict, schema: dict[str, str]) -> bool:
        if set(row) != set(schema):
            return False
        scalar_types = {"string": str, "integer": int, "boolean": bool, "object": dict}
        return all(type(row.get(key)) is scalar_types[kind] for key, kind in schema.items())

    @classmethod
    def _validate_context_rows(
        cls, rows: list, schema: dict[str, str], manifest: StoredDatasetManifest, label: str,
    ) -> None:
        received_at = _instant(manifest.retrieved_at, f"{label.lower()}_received_at")
        available_at = _instant(manifest.available_at, f"{label.lower()}_available_at")
        for row in rows:
            if (not isinstance(row, dict) or not cls._matches_schema(row, schema) or
                    row["source"] != manifest.source):
                raise DataQualityError(f"PRICE_{label}_SILVER_SCHEMA_INVALID")
            if label == "SESSION":
                try:
                    date.fromisoformat(row["exchange_local_date"])
                    opened = _instant(row["regular_open_at"], "regular_open_at")
                    closed = _instant(row["regular_close_at"], "regular_close_at")
                    _instant(row["event_time_utc"], "event_time_utc")
                except (TypeError, ValueError, DataQualityError) as exc:
                    raise DataQualityError("PRICE_SESSION_SILVER_SEMANTICS_INVALID") from exc
                if row["is_open"] and opened >= closed:
                    raise DataQualityError("PRICE_SESSION_SILVER_SEMANTICS_INVALID")
            if _instant(row["received_at"], "received_at") > received_at or \
                    _instant(row["available_at"], "available_at") > available_at:
                raise DataQualityError(f"PRICE_{label}_SILVER_TIME_INVALID")
            if label == "ACTION":
                if row["action_type"] not in {
                    "DIVIDEND", "SPLIT", "REVERSE_SPLIT", "MERGER", "SPINOFF",
                    "DELISTING", "TICKER_CHANGE",
                } or not row["terms"]:
                    raise DataQualityError("PRICE_ACTION_SILVER_SEMANTICS_INVALID")
                try:
                    date.fromisoformat(row["effective_date"])
                except ValueError as exc:
                    raise DataQualityError("PRICE_ACTION_EFFECTIVE_DATE_INVALID") from exc

    def _register_lineage(
        self, root: StoredDatasetManifest, ingested_at: datetime,
    ) -> None:
        pending: dict[str, StoredDatasetManifest] = {}

        def visit(identifier: str) -> None:
            if identifier in pending:
                return
            manifest, _ = self.datasets.read(identifier)
            pending[identifier] = manifest
            for parent_id in manifest.parent_manifest_ids:
                visit(parent_id)

        visit(root.manifest_id)
        # Parent rows are inserted before children so all FKs resolve.
        order = {"bronze": 0, "silver": 1, "gold": 2}
        for manifest in sorted(pending.values(), key=lambda item: (order[item.layer], item.manifest_id)):
            dataset_name = manifest.dataset
            manifest_runtime_id = f"phase9-manifest:{manifest.manifest_id}"
            manifest_time = _instant(manifest.available_at, "manifest_available_at")
            runtime = self.conn.execute(
                "SELECT as_of_utc, information_cutoff_utc, code_revision "
                "FROM am_runtime_run WHERE runtime_run_id=?", (manifest_runtime_id,),
            ).fetchone()
            expected_runtime = (manifest_time.isoformat(), manifest_time.isoformat(), manifest.code_revision)
            if runtime is None:
                self.conn.execute(
                    "INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
                    (manifest_runtime_id, *expected_runtime, ingested_at.isoformat()),
                )
            elif tuple(runtime) != expected_runtime:
                raise DataQualityError("PRICE_MANIFEST_RUNTIME_CONFLICT")
            ingestion_id = f"phase9-ingestion:{manifest.manifest_id}"
            ingestion = self.conn.execute(
                "SELECT runtime_run_id, provider, started_at_utc, completed_at_utc "
                "FROM am_ingestion_run WHERE ingestion_run_id=?", (ingestion_id,),
            ).fetchone()
            expected_ingestion = (manifest_runtime_id, manifest.source,
                                  manifest.retrieved_at, manifest.retrieved_at)
            if ingestion is None:
                self.conn.execute(
                    "INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
                    (ingestion_id, *expected_ingestion),
                )
            elif tuple(ingestion) != expected_ingestion:
                raise DataQualityError("PRICE_MANIFEST_INGESTION_CONFLICT")
            existing = self.conn.execute(
                    "SELECT ingestion_run_id, layer, dataset_name, uri, content_hash, observed_at_utc, "
                    "received_at_utc, schema_version, row_count FROM am_dataset_manifest "
                    "WHERE dataset_manifest_id=?", (manifest.manifest_id,),
            ).fetchone()
            values = (
                    ingestion_id, manifest.layer, dataset_name,
                    f"immutable://{manifest.manifest_id}", manifest.content_sha256,
                    manifest.available_at, manifest.retrieved_at, manifest.schema_version,
                    manifest.row_count,
            )
            if existing is None:
                self.conn.execute(
                        "INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (manifest.manifest_id, *values),
                )
            elif tuple(existing) != values:
                raise DataQualityError("PRICE_MANIFEST_REGISTRY_CONFLICT")
            for parent_id in manifest.parent_manifest_ids:
                self.conn.execute(
                        "INSERT OR IGNORE INTO am_manifest_parent VALUES (?, ?)",
                        (manifest.manifest_id, parent_id),
                )
