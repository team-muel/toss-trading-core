"""Ensure the declared CI tools reject real defects without application imports."""

from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_static_scope_and_ci_commands_are_explicit():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scope = ["src/asset_management/domain", "src/asset_management/time", "src/asset_management/config"]
    assert config["tool"]["mypy"]["files"] == scope
    assert config["tool"]["mypy"]["strict"] is True
    assert not config["tool"]["mypy"].get("ignore_missing_imports", False)
    assert config["tool"]["mypy"].get("follow_imports", "normal") == "normal"
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python -m ruff check " + " ".join(scope) in workflow
    assert "python -m mypy" in workflow
    assert workflow.index("pip install") < workflow.index("python -m mypy")


def test_mypy_rejects_type_error(tmp_path):
    source = tmp_path / "typed_sample.py"
    source.write_text('value: int = "wrong"\n', encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "mypy", "--no-incremental", "--config-file", str(ROOT / "pyproject.toml"), str(source)], capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Incompatible types in assignment" in result.stdout


def test_ruff_rejects_undefined_name(tmp_path):
    source = tmp_path / "lint_sample.py"
    source.write_text("value = missing_name\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "ruff", "check", "--config", str(ROOT / "pyproject.toml"), str(source)], capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "F821" in result.stdout
