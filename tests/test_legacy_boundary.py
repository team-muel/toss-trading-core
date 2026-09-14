import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_legacy_boundary.py"
SPEC = importlib.util.spec_from_file_location("legacy_boundary_check", SCRIPT)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def test_inventory_and_canonical_import_boundary_are_current():
    inventory = json.loads(
        (ROOT / "config/legacy_boundary_inventory.json").read_text(encoding="utf-8")
    )
    result = check.validate_inventory(ROOT, inventory)
    assert result == {
        "allowlist": {
            "src/asset_management/broker/toss_read.py": [
                "toss_trading.broker.toss",
                "toss_trading.contracts.toss",
            ]
        },
        "classified_toss_sources": 57,
        "research_cli_modules": 19,
        "research_packaging_entry_points": 17,
        "systemd_units": 7,
    }


def test_canonical_import_scanner_rejects_an_unallowlisted_import(tmp_path):
    source = tmp_path / "src/asset_management/ledger/forbidden.py"
    source.parent.mkdir(parents=True)
    source.write_text("from toss_trading.contracts.toss import require_buying_power\n", encoding="utf-8")

    assert check.boundary_import_violations(tmp_path, {}) == [
        "src/asset_management/ledger/forbidden.py:toss_trading.contracts.toss"
    ]


@pytest.mark.parametrize(
    "source_text",
    (
        "__import__('toss_trading.broker.toss')\n",
        "import importlib\nimportlib.import_module('toss_trading.broker.toss')\n",
        "name = 'toss_' + 'trading.broker.toss'\n__import__(name)\n",
        "import importlib\nname = 'toss_' + 'trading.broker.toss'\nimportlib.import_module(name)\n",
        "from importlib import import_module as load\nload('toss_trading.broker.toss')\n",
        "from builtins import __import__ as load\nload('toss_trading.broker.toss')\n",
        "import importlib as loader\nload = loader.import_module\nload('toss_trading.broker.toss')\n",
        "load = __import__\nload('toss_trading.broker.toss')\n",
        "load = globals()['__builtins__']['__import__']\nload('toss_trading.broker.toss')\n",
        (
            "import sys\n"
            "spec = next(f.find_spec('toss_trading') for f in sys.meta_path "
            "if f.find_spec('toss_trading'))\n"
            "module = spec.loader.create_module(spec)\n"
            "spec.loader.exec_module(module)\n"
        ),
        (
            "import sys\n"
            "modules = getattr(sys, 'modules')\n"
            "builtins_module = getattr(modules, 'get')('builtins')\n"
            "load = getattr(builtins_module, '__import__')\n"
            "load('toss_trading.research')\n"
        ),
        "import sys as system\ngetattr(system, 'modules')\n",
        "import sys\nsystem = sys\ngetattr(system, 'modules')\n",
    ),
)
def test_canonical_import_scanner_rejects_dynamic_legacy_imports(tmp_path, source_text):
    source = tmp_path / "src/asset_management/ledger/forbidden.py"
    source.parent.mkdir(parents=True)
    source.write_text(source_text, encoding="utf-8")

    assert check.boundary_import_violations(tmp_path, {}) == [
        "src/asset_management/ledger/forbidden.py:dynamic-import"
    ]


def test_canonical_import_scanner_allows_only_the_explicit_adapter(tmp_path):
    source = tmp_path / "src/asset_management/broker/toss_read.py"
    source.parent.mkdir(parents=True)
    source.write_text("import toss_trading.broker.toss\n", encoding="utf-8")

    assert check.boundary_import_violations(
        tmp_path,
        {
            "src/asset_management/broker/toss_read.py": {
                "toss_trading.broker.toss"
            }
        },
    ) == []


def test_canonical_import_scanner_rejects_unapproved_target_in_allowlisted_file(tmp_path):
    source = tmp_path / "src/asset_management/broker/toss_read.py"
    source.parent.mkdir(parents=True)
    source.write_text("from toss_trading.research import DataLake\n", encoding="utf-8")

    assert check.boundary_import_violations(
        tmp_path,
        {
            "src/asset_management/broker/toss_read.py": {
                "toss_trading.broker.toss"
            }
        },
    ) == ["src/asset_management/broker/toss_read.py:toss_trading.research"]


def test_canonical_rate_limiter_has_no_legacy_runtime_dependency():
    from asset_management.broker.rate_limit import TokenBucket

    bucket = TokenBucket(capacity=1, refill_per_second=1, tokens=1)
    assert bucket.try_acquire() is None
    assert bucket.try_acquire() is not None
