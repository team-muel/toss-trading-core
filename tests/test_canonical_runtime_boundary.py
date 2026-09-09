from pathlib import Path
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_retired_package_and_runtime_entrypoints_are_absent():
    tracked = subprocess.run(
        ["git", "ls-files", "--", "src/toss_trading"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert not tracked.strip()
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    assert scripts["toss-runtime-validate"] == "asset_management.cli.runtime_validate:main"
    assert not any("foundation" in name or "paper-operation" in name for name in scripts)


def test_canonical_runtime_is_read_only_and_does_not_import_retired_package():
    source = (ROOT / "src" / "asset_management" / "cli" / "runtime_validate.py").read_text(
        encoding="utf-8"
    )
    assert "ApplicationRuntime.boot" in source
    assert 'runtime_mode != "READ_ONLY"' in source
    assert "toss_trading" not in source
