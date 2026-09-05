"""Validated composition root. Domain modules never import orchestration."""

from dataclasses import dataclass
from typing import Mapping
from pathlib import Path
import sqlite3

import yaml

from asset_management.config.loader import load_policy_registry
from asset_management.config.migrations import Migrator, load_migration_catalog
from asset_management.config.schemas import PolicyRegistry
from asset_management.config.validation import ValidatedConfig, validate_startup_config
from asset_management.time.clock import Clock


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    config: ValidatedConfig
    clock: Clock
    policies: PolicyRegistry | None = None
    migration_versions: tuple[int, ...] = ()

    @classmethod
    def start(cls, raw_config: Mapping[str, object], clock: Clock) -> "ApplicationRuntime":
        """Validate every startup invariant before constructing the runtime."""

        return cls(validate_startup_config(raw_config), clock)

    @classmethod
    def boot(
        cls,
        *,
        config_path: str | Path,
        policy_registry_path: str | Path,
        schema_root: str | Path,
        conn: sqlite3.Connection,
        clock: Clock,
    ) -> "ApplicationRuntime":
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            from asset_management.domain.errors import ConfigurationError

            raise ConfigurationError("application configuration must be an object")
        config = validate_startup_config(raw)
        policies = load_policy_registry(policy_registry_path)
        required = ("data", "temporal", "promotion")
        if config.runtime_mode != "READ_ONLY":
            required += ("investment", "risk", "execution", "tax")
        policies.require_effective(required, clock.now_utc())
        catalog = load_migration_catalog(schema_root)
        applied = Migrator(conn, clock).migrate(catalog)
        return cls(config, clock, policies, applied)
