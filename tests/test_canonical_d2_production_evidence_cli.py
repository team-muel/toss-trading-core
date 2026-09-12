from pathlib import Path
import sqlite3

import pytest

from asset_management.cli.canonical_d2_production_evidence import main
from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.time.clock import FrozenClock
from datetime import datetime, timezone


def test_cli_blocks_without_a_production_evidence_database(tmp_path: Path, capsys):
    missing = tmp_path / "missing.sqlite"
    assert main(["--database", str(missing), "--immutable-store", str(tmp_path),
                 "--runtime-run-id", "run@1"]) == 2
    assert '"status": "BLOCKED"' in capsys.readouterr().out


def test_cli_has_no_caller_selected_fred_manifest_argument(tmp_path: Path):
    with pytest.raises(SystemExit):
        main(["--database", str(tmp_path / "missing.sqlite"), "--immutable-store", str(tmp_path),
              "--runtime-run-id", "run@1", "--risk-free-manifest-id", "a" * 64])


def test_cli_blocks_a_database_missing_external_provenance_schema(tmp_path: Path, capsys):
    database = tmp_path / "evidence.sqlite"
    conn = sqlite3.connect(database)
    root = Path(__file__).parents[1]
    Migrator(conn, FrozenClock(datetime(2026, 9, 12, tzinfo=timezone.utc))).migrate(
        tuple(item for item in load_migration_catalog(root / "schemas") if item.version <= 18))
    conn.close()
    assert main(["--database", str(database), "--immutable-store", str(tmp_path),
                 "--runtime-run-id", "run@1"]) == 2
    assert "CANONICAL_D2_EVIDENCE_SCHEMA_MISSING" in capsys.readouterr().out
