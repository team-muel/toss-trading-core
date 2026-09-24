from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from asset_management.cli.canonical_d2_production_evidence import _result, main
from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.time.clock import FrozenClock


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


def test_cli_receipt_hash_binds_operation_mode_and_runtime_lineage():
    value = SimpleNamespace(
        runtime_run_id="run@1",
        content_hash="a" * 64,
        model_registry_snapshot_id="registry@1",
        model_registry_binding_hash="b" * 64,
        factor_risk_calculation_id="c" * 64,
        risk_free_manifest_id="d" * 64,
        risk_free_curve_hash="e" * 64,
        accounting_snapshot_id="accounting@1",
    )
    lineage = {
        "runtime_run_id": "run@1",
        "as_of_utc": "2026-09-24T00:00:00+00:00",
        "information_cutoff_utc": "2026-09-23T23:59:00+00:00",
        "code_revision": "f" * 40,
    }
    result = _result(value, operation_mode="REPLAY_ONLY", runtime_lineage=lineage,
                     observed_at="2026-09-24T00:01:00+00:00")
    receipt = result.pop("operation_receipt_sha256")
    canonical_result = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert receipt == sha256(canonical_result.encode("utf-8")).hexdigest()
    assert result["status"] == "EVIDENCE_REPLAYED"
    assert result["operation_mode"] == "REPLAY_ONLY"
    assert result["runtime_lineage"] == lineage

    recorded = _result(value, operation_mode="RECORD", runtime_lineage=lineage,
                       observed_at="2026-09-24T00:01:00+00:00")
    assert recorded["status"] == "EVIDENCE_RECORDED"
    assert recorded["operation_mode"] == "RECORD"
