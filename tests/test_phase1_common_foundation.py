from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

import pytest

from asset_management.config.migrations import Migration, Migrator
from asset_management.config.validation import validate_startup_config
from asset_management.config.versions import content_hash
from asset_management.domain import Currency, Money, Quantity, Return, Weight
from asset_management.domain.errors import ConfigurationError, TemporalViolation
from asset_management.time.clock import FrozenClock, ReplayClock
from asset_management.orchestration.runtime import ApplicationRuntime


NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)


def valid_config() -> dict:
    return {
        "runtime_mode": "READ_ONLY",
        "base_currency": "USD",
        "reporting_currency": "KRW",
        "enabled_data_sources": ["TOSS"],
        "data_sources": {"TOSS": {"kind": "broker"}},
        "parameter_set": {"id": "foundation-v1", "status": "APPROVED"},
        "risk_limits": {"max_open_orders": "1"},
        "live_trading_enabled": False,
    }


def test_exact_types_reject_float_and_invalid_ranges():
    assert Money.of("1.235", "USD").amount == Decimal("1.24")
    assert Quantity.of("1.25").value == Decimal("1.25")
    assert Weight.of("0.25").value == Decimal("0.25")
    assert Return.of("-1").value == Decimal("-1")
    assert Currency.parse("usd") is Currency.USD
    for factory, value in ((Weight.of, "1.01"), (Return.of, "-1.01"), (Quantity.of, "-0.1")):
        with pytest.raises(Exception):
            factory(value)
    with pytest.raises(TypeError):
        Weight.of(0.1)


def test_replay_clock_is_deterministic_and_cannot_reverse():
    clock = ReplayClock(NOW)
    clock.advance_to(NOW + timedelta(seconds=1))
    assert clock.now_utc() == NOW + timedelta(seconds=1)
    with pytest.raises(TemporalViolation):
        clock.advance_to(NOW)
    assert FrozenClock(NOW).now_utc() == NOW


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda c: c.pop("runtime_mode"), "missing"),
        (lambda c: c.update(runtime_mode="LIVE"), "not allowed"),
        (lambda c: c["risk_limits"].update(max_open_orders="-1"), "negative"),
        (lambda c: c["risk_limits"].update(max_open_orders="not-a-number"), "invalid risk"),
        (lambda c: c.update(base_currency="XYZ"), "unsupported currency"),
        (lambda c: c.update(enabled_data_sources=["UNKNOWN"]), "undefined data"),
        (lambda c: c.update(parameter_set={"id": "x", "status": "DRAFT"}), "APPROVED"),
    ],
)
def test_invalid_startup_config_fails_immediately(mutation, message):
    config = valid_config()
    mutation(config)
    with pytest.raises(ConfigurationError, match=message):
        validate_startup_config(config)


def test_valid_startup_config_is_typed_and_live_disabled():
    config = validate_startup_config(valid_config())
    assert config.base_currency is Currency.USD
    assert config.risk_limits["max_open_orders"] == Decimal("1")
    assert config.live_trading_enabled is False
    assert ApplicationRuntime.start(valid_config(), FrozenClock(NOW)).config == config


def test_canonical_hash_is_reproducible_across_mapping_order():
    assert content_hash({"b": 2, "a": {"y": 2, "x": 1}}) == content_hash(
        {"a": {"x": 1, "y": 2}, "b": 2}
    )


def test_migrations_are_replay_safe_and_preserve_hashes():
    conn = sqlite3.connect(":memory:")
    migrations = (
        Migration(1, "create_item", "CREATE TABLE item(id INTEGER PRIMARY KEY);"),
        Migration(2, "add_name", "ALTER TABLE item ADD COLUMN name TEXT;"),
    )
    migrator = Migrator(conn, FrozenClock(NOW))
    assert migrator.migrate(migrations) == (1, 2)
    assert migrator.migrate(migrations) == ()
    assert conn.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0] == 2
    changed = (migrations[0], Migration(2, "add_name", "SELECT 1;"))
    with pytest.raises(ConfigurationError, match="changed"):
        migrator.migrate(changed)


def test_failed_migration_is_not_recorded_or_partially_applied():
    conn = sqlite3.connect(":memory:")
    migrator = Migrator(conn, FrozenClock(NOW))
    with pytest.raises(sqlite3.DatabaseError):
        migrator.migrate((Migration(1, "broken", "CREATE TABLE transient(id); INVALID SQL;"),))
    assert conn.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='transient'"
    ).fetchone()[0] == 0


def test_migration_sequence_cannot_have_gaps():
    with pytest.raises(ConfigurationError, match="contiguous"):
        Migrator(sqlite3.connect(":memory:"), FrozenClock(NOW)).migrate(
            (Migration(1, "one", "SELECT 1;"), Migration(3, "three", "SELECT 3;"))
        )


def test_migration_sequence_cannot_start_after_one():
    with pytest.raises(ConfigurationError, match="start at version 1"):
        Migrator(sqlite3.connect(":memory:"), FrozenClock(NOW)).migrate(
            (Migration(2, "two", "SELECT 2;"),)
        )
