import sqlite3
import json
from pathlib import Path

from asset_management.cli.ama150_production_readiness import main as readiness_main
from asset_management.validation.forecast_production_readiness import (
    MINIMUM_INPUTS, SOURCE_REQUIREMENTS, evaluate_production_readiness,
)


def test_empty_environment_is_explicitly_blocked_without_creating_evidence(tmp_path: Path) -> None:
    database = tmp_path / "missing.sqlite"
    report = evaluate_production_readiness(
        connection=None, immutable_store=tmp_path / "missing-store", runtime_run_id=None, environment={})

    assert report["status"] == "BLOCKED"
    assert report["runtime"]["reasons"] == ["EVIDENCE_DATABASE_MISSING"]
    assert report["sample_ingest"]["does_not_create_economic_inputs"] is True
    assert not database.exists()
    assert "CANONICAL_PRODUCTION_RUN" in report["prohibitions"]


def test_selected_collector_manifest_aliases_are_verified_but_do_not_promote_unassigned_contracts(tmp_path: Path) -> None:
    root = tmp_path / "research"
    manifests = root / "catalog" / "manifests"
    manifests.mkdir(parents=True)
    (root / "bronze").mkdir()
    (root / "silver").mkdir()
    (manifests / "tiingo-bronze.json").write_text('{"source":"tiingo-eod","layer":"bronze"}', encoding="utf-8")
    (manifests / "tiingo-silver.json").write_text('{"source":"tiingo-eod","layer":"silver"}', encoding="utf-8")
    (manifests / "fred-bronze.json").write_text('{"source":"fred-alfred","layer":"bronze"}', encoding="utf-8")
    (manifests / "fred-silver.json").write_text('{"source":"fred-alfred","layer":"silver"}', encoding="utf-8")

    report = evaluate_production_readiness(
        connection=None, immutable_store=root, runtime_run_id="run@1",
        environment={"TIINGO_API_TOKEN": "present", "FRED_API_KEY": "present"})

    assert report["sample_ingest"]["state"] == "AVAILABLE"
    assert report["sample_ingest"]["sources"]["fred_alfred"]["manifest_source"] == "fred-alfred"
    assert report["status"] == "BLOCKED"
    assert "PROVIDER_QUALIFICATION_OR_CREDENTIALS_INCOMPLETE" in report["reasons"]


def test_minimum_inputs_preserve_physical_gld_no_roll_zero_shortcut() -> None:
    gld = [item.component for item in MINIMUM_INPUTS if item.instrument_id == "GLD"]
    assert gld == ["spot_change", "carry", "expense"]
    assert "roll_yield" not in gld
    assert SOURCE_REQUIREMENTS["approved_spot_market"].state == "UNASSIGNED"


def test_schema_missing_is_blocked_before_runtime_or_model_queries() -> None:
    connection = sqlite3.connect(":memory:")
    report = evaluate_production_readiness(
        connection=connection, immutable_store=None, runtime_run_id="run@1", environment={})

    assert report["status"] == "BLOCKED"
    assert report["runtime"]["state"] == "UNAVAILABLE"
    assert report["runtime"]["reasons"][0].startswith("EVIDENCE_SCHEMA_MISSING:")


def test_unreadable_database_is_normalized_to_blocked_json(tmp_path: Path, capsys) -> None:
    database = tmp_path / "not-sqlite.sqlite"
    database.write_bytes(b"not a sqlite database")

    assert readiness_main(["--database", str(database)]) == 2

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BLOCKED"
    assert report["runtime"]["reasons"] == ["EVIDENCE_DATABASE_UNREADABLE"]
