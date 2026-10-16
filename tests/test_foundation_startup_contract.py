"""AMA-12 startup rejection must precede all evidence/database mutation."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
import yaml

from asset_management.domain.errors import ConfigurationError
from asset_management.orchestration.runtime import ApplicationRuntime
from asset_management.time.clock import FrozenClock

ROOT = Path(__file__).resolve().parents[1]
RAW = yaml.safe_load((ROOT / "config/application.yaml").read_text(encoding="utf-8"))
NOW = datetime(2026, 9, 4, 1, tzinfo=timezone.utc)


def boot(path, connection):
    return ApplicationRuntime.boot(
        config_path=path, policy_registry_path=ROOT / "config/policy_registry.yaml",
        schema_root=ROOT / "schemas", conn=connection, clock=FrozenClock(NOW),
    )


def assert_rejected_without_db_mutation(tmp_path, raw):
    path = tmp_path / "application.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with sqlite3.connect(":memory:") as connection:
        with pytest.raises(ConfigurationError):
            boot(path, connection)
        assert connection.total_changes == 0
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


@pytest.mark.parametrize("field", sorted(RAW))
def test_every_required_startup_field_is_fail_closed(tmp_path, field):
    raw = deepcopy(RAW)
    del raw[field]
    assert_rejected_without_db_mutation(tmp_path, raw)


@pytest.mark.parametrize("updates", [
    {"runtime_mode": "LIVE"},
    {"live_trading_enabled": True},
    {"live_trading_enabled": 0},
    {"base_currency": "UNKNOWN"},
    {"enabled_data_sources": "TOSS"},
    {"enabled_data_sources": ["UNDEFINED"]},
    {"parameter_set": {"id": "test", "status": "DRAFT"}},
    {"parameter_set": {"id": "", "status": "APPROVED"}},
    {"risk_limits": {"max_open_orders": 1.0}},
    {"risk_limits": {"max_open_orders": "-1"}},
    {"risk_limits": {"max_open_orders": "NaN"}},
])
def test_invalid_or_unapproved_config_cannot_boot(tmp_path, updates):
    raw = deepcopy(RAW)
    raw.update(updates)
    assert_rejected_without_db_mutation(tmp_path, raw)


@pytest.mark.parametrize("raw", [None, [], "invalid"])
def test_nonobject_configuration_cannot_boot(tmp_path, raw):
    assert_rejected_without_db_mutation(tmp_path, raw)


def test_missing_configuration_file_fails_before_migration(tmp_path):
    with sqlite3.connect(":memory:") as connection:
        with pytest.raises(FileNotFoundError):
            boot(tmp_path / "missing.yaml", connection)
        assert connection.total_changes == 0
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_read_only_defaults_and_parameter_identity_survive_boot():
    with sqlite3.connect(":memory:") as connection:
        runtime = boot(ROOT / "config/application.yaml", connection)
        assert runtime.config.runtime_mode == "READ_ONLY"
        assert runtime.config.live_trading_enabled is False
        assert runtime.config.parameter_set_id == RAW["parameter_set"]["id"]
        recorded = connection.execute("SELECT version, name, content_hash, applied_at_utc FROM schema_migration ORDER BY version").fetchall()
        assert [row[0] for row in recorded] == list(range(1, 21))
        second = boot(ROOT / "config/application.yaml", connection)
        assert second.migration_versions == ()
        assert connection.execute("SELECT version, name, content_hash, applied_at_utc FROM schema_migration ORDER BY version").fetchall() == recorded
