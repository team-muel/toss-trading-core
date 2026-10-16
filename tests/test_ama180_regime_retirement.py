from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

from asset_management.states import MarketStateEngine


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_NAMESPACES = ("toss_trading.research", "research_platform")
RESEARCH_SOURCE_ROOTS = (
    ROOT / "src" / "toss_trading" / "research",
    ROOT / "src" / "research_platform",
)


def _available_research_modules():
    for name in RESEARCH_NAMESPACES:
        if importlib.util.find_spec(name) is not None:
            yield importlib.import_module(name)


def test_canonical_state_schema_has_fully_retired_legacy_regime_label():
    schema = json.loads((ROOT / "schemas" / "state_snapshot.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == "state-snapshot-v3"
    assert "regime_label" not in schema["required"]
    assert "regime_label" not in schema["properties"]
    assert not hasattr(MarketStateEngine(), "_derive_regime")


def test_legacy_macro_allocation_is_not_on_any_public_research_surface():
    modules = list(_available_research_modules())
    assert modules
    for module in modules:
        assert not hasattr(module, "MacroRegimeConfig")
        assert not hasattr(module, "run_macro_regime_backtest")


def test_active_research_policy_cannot_select_legacy_macro_regime():
    policy = json.loads(
        (ROOT / "config" / "autonomous_research_policy.json").read_text(encoding="utf-8")
    )
    assert "macro_regime" not in policy["strategy_families"]
    assert "macro_regime" not in policy["family_rotation"]
    assert all(not key.startswith("allowed_macro_") for key in policy)
    assert "macro_publication_lag_days" not in policy


def test_state_risk_multiplier_has_no_production_consumer_outside_states_package():
    offenders: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("src/asset_management/states/"):
            continue
        source = path.read_text(encoding="utf-8")
        if "risk_multiplier" in source:
            offenders.append(relative)
    assert offenders == []


def test_legacy_macro_backtest_remains_direct_historical_replay_only():
    historical_backtests = [
        root / "backtest.py" for root in RESEARCH_SOURCE_ROOTS if (root / "backtest.py").is_file()
    ]
    assert historical_backtests
    for backtest_path in historical_backtests:
        backtest = backtest_path.read_text(encoding="utf-8")
        assert "class MacroRegimeConfig" in backtest
        assert "def run_macro_regime_backtest" in backtest

    active_evaluators = [
        root / "candidate_evaluation.py"
        for root in RESEARCH_SOURCE_ROOTS
        if (root / "candidate_evaluation.py").is_file()
    ]
    assert active_evaluators
    for evaluator_path in active_evaluators:
        active_evaluator = evaluator_path.read_text(encoding="utf-8")
        assert "LEGACY_MACRO_REGIME_READ_ONLY" in active_evaluator
        assert "run_macro_regime_backtest" not in active_evaluator
