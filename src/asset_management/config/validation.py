from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from asset_management.domain.decimal import exact_decimal
from asset_management.domain.errors import ConfigurationError, InvariantViolation
from asset_management.domain.scalars import Currency


ALLOWED_RUNTIME_MODES = {"READ_ONLY", "REPLAY", "PAPER", "SHADOW"}


@dataclass(frozen=True, slots=True)
class ValidatedConfig:
    runtime_mode: str
    base_currency: Currency
    reporting_currency: Currency
    enabled_data_sources: tuple[str, ...]
    parameter_set_id: str
    risk_limits: Mapping[str, Decimal]
    live_trading_enabled: bool


def _mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"required configuration object is missing: {key}")
    return value


def validate_startup_config(raw: Mapping[str, object]) -> ValidatedConfig:
    required = {
        "runtime_mode", "base_currency", "reporting_currency", "enabled_data_sources",
        "data_sources", "parameter_set", "risk_limits", "live_trading_enabled",
    }
    missing = required - raw.keys()
    if missing:
        raise ConfigurationError(f"required configuration fields missing: {sorted(missing)}")

    mode = str(raw["runtime_mode"]).upper()
    if mode not in ALLOWED_RUNTIME_MODES:
        raise ConfigurationError(f"runtime mode is not allowed: {mode}")
    if raw["live_trading_enabled"] is not False:
        raise ConfigurationError("live trading must remain disabled")
    try:
        base = Currency.parse(str(raw["base_currency"]))
        reporting = Currency.parse(str(raw["reporting_currency"]))
    except Exception as exc:
        raise ConfigurationError(str(exc)) from exc

    enabled_raw = raw["enabled_data_sources"]
    if isinstance(enabled_raw, (str, bytes)) or not isinstance(enabled_raw, Sequence):
        raise ConfigurationError("enabled_data_sources must be a list")
    enabled = tuple(str(item) for item in enabled_raw)
    sources = _mapping(raw, "data_sources")
    undefined = sorted(set(enabled) - set(sources))
    if undefined:
        raise ConfigurationError(f"undefined data sources enabled: {undefined}")

    parameter_set = _mapping(raw, "parameter_set")
    parameter_id = str(parameter_set.get("id", "")).strip()
    if not parameter_id or parameter_set.get("status") != "APPROVED":
        raise ConfigurationError("parameter set must exist and be APPROVED")

    limits_raw = _mapping(raw, "risk_limits")
    limits: dict[str, Decimal] = {}
    for name, value in limits_raw.items():
        try:
            limit = exact_decimal(value)  # type: ignore[arg-type]
        except (TypeError, ValueError, ConfigurationError, InvariantViolation) as exc:
            raise ConfigurationError(f"invalid risk limit {name}: {value!r}") from exc
        if limit < 0:
            raise ConfigurationError(f"risk limit cannot be negative: {name}")
        limits[str(name)] = limit
    return ValidatedConfig(mode, base, reporting, enabled, parameter_id, limits, False)
