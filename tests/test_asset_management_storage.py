from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from asset_management.data.layout import DataLakeLayout
from asset_management.data.manifests import DatasetManifest
from asset_management.decisions.journal import DecisionLineage


SCHEMA = Path(__file__).parents[1] / "schemas" / "asset_management.sql"


def database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def test_manifest_validates_layer_and_point_in_time_order():
    received = datetime(2026, 1, 2, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        DatasetManifest("m", "i", "raw", "prices", "x", "h", received, received)
    with pytest.raises(ValueError):
        DatasetManifest(
            "m", "i", "bronze", "prices", "x", "h",
            datetime(2026, 1, 3, tzinfo=timezone.utc), received,
        )


def test_data_lake_layout_cannot_escape_layer(tmp_path):
    layout = DataLakeLayout(tmp_path)
    assert layout.resolve("bronze", "provider/day.parquet").parent.name == "provider"
    with pytest.raises(ValueError):
        layout.resolve("bronze", "../silver/leak.parquet")


def test_storage_schema_enforces_lineage_and_append_only_manifest():
    conn = database()
    conn.execute(
        "INSERT INTO am_runtime_run VALUES (?, ?, ?, ?, ?)",
        ("run", "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z", "sha", "2026-01-02T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO am_ingestion_run VALUES (?, ?, ?, ?, ?)",
        ("ingest", "run", "provider", "2026-01-02T00:00:00Z", "2026-01-02T00:01:00Z"),
    )
    conn.execute(
        "INSERT INTO am_dataset_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("manifest", "ingest", "bronze", "prices", "bronze/prices.parquet", "hash", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "1", 1),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE am_dataset_manifest SET row_count = 2 WHERE dataset_manifest_id = 'manifest'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO am_feature_run VALUES (?, ?, ?, ?, ?, ?)",
            ("feature", "run", "missing-manifest", "v1", "{}", "hash"),
        )


def test_decision_lineage_preserves_order_to_source_chain():
    lineage = DecisionLineage(
        "runtime", "ingestion", "manifest", "feature", "state", "pricing",
        "expectation", "risk", "target", "decision", "intent", "client",
        "broker", "execution", "reconciliation",
    )
    assert lineage.decision_chain()[0] == "runtime"
    assert lineage.decision_chain()[-1] == "reconciliation"
    assert len(lineage.decision_chain()) == 15
