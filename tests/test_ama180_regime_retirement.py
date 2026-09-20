from __future__ import annotations

import json
from pathlib import Path

import toss_trading.research as research

from asset_management.states import MarketStateEngine


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_state_schema_has_fully_retired_legacy_regime_label():
    schema = json.loads((ROOT / "schemas" / "state_snapshot.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == "state-snapshot-v3"
    assert "regime_label" not in schema["required"]
    assert "regime_label" not in schema["properties"]
    assert not hasattr(MarketStateEngine(), "_derive_regime")


def test_legacy_macro_allocation_is_not_on_public_research_surface():
    assert not hasattr(research, "MacroRegimeConfig")
    assert not hasattr(research, "run_macro_regime_backtest")


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
    backtest = (ROOT / "src" / "toss_trading" / "research" / "backtest.py").read_text(
        encoding="utf-8"
    )
    assert "class MacroRegimeConfig" in backtest
    assert "def run_macro_regime_backtest" in backtest

    active_evaluator = (
        ROOT / "src" / "toss_trading" / "research" / "candidate_evaluation.py"
    ).read_text(encoding="utf-8")
    assert "LEGACY_MACRO_REGIME_READ_ONLY" in active_evaluator
    assert "run_macro_regime_backtest" not in active_evaluator
