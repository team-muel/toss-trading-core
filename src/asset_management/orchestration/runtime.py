"""Validated composition root. Domain modules never import orchestration."""

from dataclasses import dataclass
from typing import Mapping

from asset_management.config.validation import ValidatedConfig, validate_startup_config
from asset_management.time.clock import Clock


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    config: ValidatedConfig
    clock: Clock

    @classmethod
    def start(cls, raw_config: Mapping[str, object], clock: Clock) -> "ApplicationRuntime":
        """Validate every startup invariant before constructing the runtime."""

        return cls(validate_startup_config(raw_config), clock)
