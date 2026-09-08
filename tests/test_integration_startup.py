from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from asset_management.domain.errors import ConfigurationError
from asset_management.orchestration.runtime import ApplicationRuntime
from asset_management.config.loader import load_policy_registry
from asset_management.time.clock import FrozenClock


ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 9, 4, 1, tzinfo=timezone.utc)


def test_real_read_only_configuration_boots_and_applies_migrations_once():
    conn = sqlite3.connect(":memory:")
    kwargs = dict(
        config_path=ROOT / "config/application.yaml",
        policy_registry_path=ROOT / "config/policy_registry.yaml",
        schema_root=ROOT / "schemas",
        conn=conn,
        clock=FrozenClock(NOW),
    )
    first = ApplicationRuntime.boot(**kwargs)
    second = ApplicationRuntime.boot(**kwargs)
    assert first.migration_versions == tuple(range(1, 14))
    assert second.migration_versions == ()
    assert first.policies is not None
    assert first.policies.policies["data"].is_effective(NOW)


def test_draft_trading_policies_block_non_read_only_boot(tmp_path):
    config = (ROOT / "config/application.yaml").read_text(encoding="utf-8").replace(
        "runtime_mode: READ_ONLY", "runtime_mode: PAPER"
    )
    path = tmp_path / "application.yaml"
    path.write_text(config, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not approved"):
        ApplicationRuntime.boot(
            config_path=path,
            policy_registry_path=ROOT / "config/policy_registry.yaml",
            schema_root=ROOT / "schemas",
            conn=sqlite3.connect(":memory:"),
            clock=FrozenClock(NOW),
        )


def test_policy_document_tampering_is_detected(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    for source in (ROOT / "docs").glob("*_policy.md"):
        (docs / source.name).write_bytes(source.read_bytes())
    registry_dir = tmp_path / "config"
    registry_dir.mkdir()
    registry = registry_dir / "policy_registry.yaml"
    registry.write_bytes((ROOT / "config/policy_registry.yaml").read_bytes())
    (docs / "data_policy.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_policy_registry(registry, repository_root=tmp_path)
