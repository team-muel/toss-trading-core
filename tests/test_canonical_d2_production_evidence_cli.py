from pathlib import Path

from asset_management.cli.canonical_d2_production_evidence import main


def test_cli_blocks_without_a_production_evidence_database(tmp_path: Path, capsys):
    missing = tmp_path / "missing.sqlite"
    assert main(["--database", str(missing), "--immutable-store", str(tmp_path),
                 "--runtime-run-id", "run@1", "--risk-free-manifest-id", "a" * 64]) == 2
    assert '"status": "BLOCKED"' in capsys.readouterr().out
