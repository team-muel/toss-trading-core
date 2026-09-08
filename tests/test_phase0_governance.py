from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).parents[1]


def test_governance_contract_is_complete_and_live_is_disabled():
    result = subprocess.run(
        [sys.executable, "scripts/check_governance.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    registry = yaml.safe_load((ROOT / "config/policy_registry.yaml").read_text(encoding="utf-8"))
    foundation = yaml.safe_load((ROOT / "config/default_policy.yaml").read_text(encoding="utf-8"))
    assert registry["live_trading_enabled"] is False
    assert foundation["runtime"]["live_trading_enabled"] is False
    assert registry["policies"]["investment"]["status"] == "DRAFT"
    assert registry["policies"]["risk"]["status"] == "DRAFT"
    assert registry["policies"]["execution"]["status"] == "DRAFT"


def test_live_adapter_fails_closed():
    from asset_management.broker.toss_write import DisabledTossWriteAdapter
    from asset_management.domain.errors import NoTrade

    adapter = DisabledTossWriteAdapter()
    try:
        adapter.submit({}, idempotency_key="test")
    except NoTrade:
        return
    raise AssertionError("live adapter must not submit an order")
