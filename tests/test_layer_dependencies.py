from pathlib import Path

import pytest

from scripts.check_layer_dependencies import check_layers, main

ROOT = Path(__file__).resolve().parents[1]


def source(root, layer, text):
    path = root / "src" / "asset_management" / layer / "sample.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_current_repository_obeys_layers():
    assert check_layers(ROOT)["checked_sources"] > 100


@pytest.mark.parametrize("text", [
    "import asset_management.execution.intents as orders",
    "from asset_management.execution import intents",
    "from asset_management import execution",
    "from ..execution import intents",
    "from .. import execution",
    "if False:\n    from asset_management.execution import intents",
    "from asset_management.validation import account_truth",
    "from asset_management import *",
    "import asset_management as core",
    "from asset_management.unreviewed import module",
])
def test_core_outward_or_unknown_imports_fail_closed(tmp_path, text):
    source(tmp_path, "features", text)
    with pytest.raises(ValueError):
        check_layers(tmp_path)
    assert main(["--root", str(tmp_path)]) == 1


@pytest.mark.parametrize("layer,text", [
    ("domain", "import asset_management.config"),
    ("execution", "from asset_management.pricing import capm"),
    ("execution", "from asset_management.features import models"),
    ("new_layer", "pass"),
])
def test_layer_contract_and_execution_boundary(tmp_path, layer, text):
    source(tmp_path, layer, text)
    with pytest.raises(ValueError):
        check_layers(tmp_path)


@pytest.mark.parametrize("layer", ["domain", "features", "execution", "governance"])
@pytest.mark.parametrize("text", [
    "import alpha_management",
    "import alpha_management.history as history",
    "from alpha_management.history import returns",
    "if False:\n    from alpha_management import history",
])
def test_core_cannot_import_sibling_research(tmp_path, layer, text):
    source(tmp_path, layer, text)
    with pytest.raises(ValueError, match="core cannot import sibling research"):
        check_layers(tmp_path)


@pytest.mark.parametrize("layer,text", [
    ("features", "from asset_management.domain import errors"),
    ("features", "from ..domain import errors"),
    ("features", "from . import models"),
    ("features", "from asset_management.features import models"),
    ("execution", "from asset_management.decisions import governor"),
    ("orchestration", "from asset_management.execution import intents"),
    ("orchestration", "from asset_management.validation import account_truth"),
    ("orchestration", "from alpha_management.history import returns"),
])
def test_inward_same_layer_and_shell_composition_allowed(tmp_path, layer, text):
    source(tmp_path, layer, text)
    assert check_layers(tmp_path)["checked_sources"] == 1


def test_missing_sources_and_invalid_syntax_block(tmp_path):
    assert main(["--root", str(tmp_path)]) == 1
    source(tmp_path, "features", "from broken")
    assert main(["--root", str(tmp_path)]) == 1


def test_recovery_contract_and_root_module_classification(tmp_path):
    root = tmp_path / "src/asset_management"
    root.mkdir(parents=True)
    (root / "recovery.py").write_text("from asset_management.data import immutable", encoding="utf-8")
    assert check_layers(tmp_path)["checked_sources"] == 1
    (root / "unknown.py").write_text("pass", encoding="utf-8")
    with pytest.raises(ValueError, match="unclassified layer"):
        check_layers(tmp_path)


def test_nested_package_relative_imports_resolve_to_actual_layer(tmp_path):
    path = tmp_path / "src/asset_management/features/nested/__init__.py"
    path.parent.mkdir(parents=True)
    path.write_text("from ...domain import errors", encoding="utf-8")
    assert check_layers(tmp_path)["checked_sources"] == 1
    path.write_text("from ...execution import intents", encoding="utf-8")
    with pytest.raises(ValueError, match="outward core dependency"):
        check_layers(tmp_path)


def test_root_reexport_and_escaping_relative_import_block(tmp_path):
    path = tmp_path / "src/asset_management/__init__.py"
    path.parent.mkdir(parents=True)
    path.write_text("from .domain import errors", encoding="utf-8")
    with pytest.raises(ValueError, match="package root"):
        check_layers(tmp_path)
    path.write_text("from ..execution import intents", encoding="utf-8")
    with pytest.raises(ValueError, match="relative import escapes"):
        check_layers(tmp_path)


def test_ci_runs_layer_check_before_dependencies():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert workflow.index("python scripts/check_layer_dependencies.py") < workflow.index("pip install")
