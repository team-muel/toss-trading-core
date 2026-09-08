"""Immutable Strategy contracts, lifecycle, capital budgets, and attribution."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation


class StrategyStatus(StrEnum):
    RESEARCH = "RESEARCH"
    CANDIDATE = "CANDIDATE"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"
    DEGRADED = "DEGRADED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class StrategyRuntimeMode(StrEnum):
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


_TRANSITIONS = {
    StrategyStatus.RESEARCH: {StrategyStatus.CANDIDATE, StrategyStatus.RETIRED},
    StrategyStatus.CANDIDATE: {StrategyStatus.RESEARCH, StrategyStatus.PAPER, StrategyStatus.SUSPENDED, StrategyStatus.RETIRED},
    StrategyStatus.PAPER: {StrategyStatus.CANDIDATE, StrategyStatus.SHADOW, StrategyStatus.DEGRADED, StrategyStatus.SUSPENDED, StrategyStatus.RETIRED},
    StrategyStatus.SHADOW: {StrategyStatus.PAPER, StrategyStatus.LIVE, StrategyStatus.DEGRADED, StrategyStatus.SUSPENDED, StrategyStatus.RETIRED},
    StrategyStatus.LIVE: {StrategyStatus.SHADOW, StrategyStatus.DEGRADED, StrategyStatus.SUSPENDED, StrategyStatus.RETIRED},
    StrategyStatus.DEGRADED: {StrategyStatus.PAPER, StrategyStatus.SHADOW, StrategyStatus.SUSPENDED, StrategyStatus.RETIRED},
    StrategyStatus.SUSPENDED: {StrategyStatus.CANDIDATE, StrategyStatus.PAPER, StrategyStatus.RETIRED},
    StrategyStatus.RETIRED: set(),
}


def _identifiers(values: tuple[str, ...], reason: str) -> tuple[str, ...]:
    if (not values or len(values) != len(set(values)) or
            any(not isinstance(value, str) or not value.strip() for value in values)):
        raise InvariantViolation(reason)
    return tuple(sorted(values))


def _decimal(value: Decimal, reason: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantViolation(reason)
    return value


@dataclass(frozen=True, slots=True)
class CapitalRiskBudget:
    capital_fraction: Decimal
    risk_fraction: Decimal
    max_gross_leverage: Decimal
    max_turnover: Decimal

    def __post_init__(self) -> None:
        if (not Decimal(0) < _decimal(self.capital_fraction, "STRATEGY_BUDGET_INVALID") <= Decimal(1) or
                not Decimal(0) < _decimal(self.risk_fraction, "STRATEGY_BUDGET_INVALID") <= Decimal(1) or
                _decimal(self.max_gross_leverage, "STRATEGY_BUDGET_INVALID") <= 0 or
                not Decimal(0) <= _decimal(self.max_turnover, "STRATEGY_BUDGET_INVALID") <= Decimal(1)):
            raise InvariantViolation("STRATEGY_BUDGET_INVALID")

    def payload(self) -> dict[str, str]:
        return {"capital_fraction": str(self.capital_fraction), "risk_fraction": str(self.risk_fraction),
                "max_gross_leverage": str(self.max_gross_leverage), "max_turnover": str(self.max_turnover)}


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    strategy_id: str
    version: str
    name: str
    economic_thesis: str
    investable_universe_key: str
    signal_keys: tuple[str, ...]
    forecast_combination_key: str
    pricing_model_key: str
    risk_model_key: str
    portfolio_policy_key: str
    execution_policy_key: str
    benchmark: str
    budget: CapitalRiskBudget
    supported_horizon: int
    currency: str
    allowed_runtime_modes: tuple[StrategyRuntimeMode, ...]
    effective_from: datetime
    effective_to: datetime
    status: StrategyStatus = StrategyStatus.RESEARCH

    def __post_init__(self) -> None:
        text = (self.strategy_id, self.version, self.name, self.economic_thesis,
                self.investable_universe_key, self.forecast_combination_key, self.pricing_model_key,
                self.risk_model_key, self.portfolio_policy_key, self.execution_policy_key, self.benchmark)
        if (any(not isinstance(value, str) or not value.strip() for value in text) or
                not isinstance(self.budget, CapitalRiskBudget) or
                self.supported_horizon not in {21, 63, 126, 252} or self.currency != "USD" or
                not isinstance(self.status, StrategyStatus) or
                not self.allowed_runtime_modes or len(set(self.allowed_runtime_modes)) != len(self.allowed_runtime_modes) or
                any(not isinstance(mode, StrategyRuntimeMode) for mode in self.allowed_runtime_modes) or
                not isinstance(self.effective_from, datetime) or not isinstance(self.effective_to, datetime) or
                self.effective_from.tzinfo is None or self.effective_from.utcoffset() is None or
                self.effective_to.tzinfo is None or self.effective_to.utcoffset() is None):
            raise InvariantViolation("STRATEGY_DEFINITION_INVALID")
        start, end = self.effective_from.astimezone(timezone.utc), self.effective_to.astimezone(timezone.utc)
        if end <= start:
            raise InvariantViolation("STRATEGY_EFFECTIVE_WINDOW_INVALID")
        object.__setattr__(self, "signal_keys", _identifiers(self.signal_keys, "STRATEGY_SIGNAL_KEYS_INVALID"))
        object.__setattr__(self, "allowed_runtime_modes", tuple(sorted(set(self.allowed_runtime_modes),
                                                                     key=lambda mode: mode.value)))
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_to", end)

    @property
    def key(self) -> str:
        return f"{self.strategy_id}@{self.version}"

    def payload(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id, "version": self.version, "name": self.name,
            "economic_thesis": self.economic_thesis, "investable_universe_key": self.investable_universe_key,
            "signal_keys": list(self.signal_keys), "forecast_combination_key": self.forecast_combination_key,
            "pricing_model_key": self.pricing_model_key, "risk_model_key": self.risk_model_key,
            "portfolio_policy_key": self.portfolio_policy_key, "execution_policy_key": self.execution_policy_key,
            "benchmark": self.benchmark, "budget": self.budget.payload(),
            "supported_horizon": self.supported_horizon, "currency": self.currency,
            "allowed_runtime_modes": [mode.value for mode in self.allowed_runtime_modes],
            "effective_from": self.effective_from.isoformat(), "effective_to": self.effective_to.isoformat(),
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class StrategyTransition:
    strategy_key: str
    from_status: StrategyStatus
    to_status: StrategyStatus
    effective_at: datetime
    reason: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.strategy_key, str) or not self.strategy_key.strip() or
                not isinstance(self.from_status, StrategyStatus) or not isinstance(self.to_status, StrategyStatus) or
                not isinstance(self.reason, str) or not self.reason.strip() or
                not isinstance(self.effective_at, datetime) or
                self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None):
            raise InvariantViolation("STRATEGY_TRANSITION_INVALID")
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(timezone.utc))
        object.__setattr__(self, "evidence_ids", _identifiers(self.evidence_ids, "STRATEGY_TRANSITION_EVIDENCE_INVALID"))

    def payload(self) -> dict[str, object]:
        return {"strategy_key": self.strategy_key, "from_status": self.from_status.value,
                "to_status": self.to_status.value, "effective_at": self.effective_at.isoformat(),
                "reason": self.reason, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True, slots=True)
class StrategyAuthorization:
    strategy_key: str
    runtime_mode: StrategyRuntimeMode
    registry_hash: str
    authorized_at: str
    authorization_hash: str


@dataclass(frozen=True, slots=True)
class StrategyAttribution:
    strategy_key: str
    as_of: datetime
    gross_return: Decimal
    implementation_cost: Decimal
    net_return: Decimal
    benchmark_return: Decimal
    forecast_component_ids: tuple[str, ...]
    attribution_hash: str

    def __post_init__(self) -> None:
        if (not isinstance(self.strategy_key, str) or not self.strategy_key.strip() or
                not isinstance(self.as_of, datetime) or
                self.as_of.tzinfo is None or self.as_of.utcoffset() is None or
                any(not isinstance(value, Decimal) or not value.is_finite()
                    for value in (self.gross_return, self.implementation_cost, self.net_return, self.benchmark_return)) or
                self.implementation_cost < 0 or self.net_return != self.gross_return - self.implementation_cost):
            raise InvariantViolation("STRATEGY_ATTRIBUTION_INVALID")
        object.__setattr__(self, "as_of", self.as_of.astimezone(timezone.utc))
        object.__setattr__(self, "forecast_component_ids", _identifiers(
            self.forecast_component_ids, "STRATEGY_ATTRIBUTION_LINEAGE_INVALID"))
        body = self.payload(include_hash=False)
        if self.attribution_hash != digest(canonical(body)):
            raise InvariantViolation("STRATEGY_ATTRIBUTION_HASH_INVALID")

    def payload(self, *, include_hash: bool = True) -> dict[str, object]:
        body = {"strategy_key": self.strategy_key, "as_of": self.as_of.isoformat(),
                "gross_return": str(self.gross_return), "implementation_cost": str(self.implementation_cost),
                "net_return": str(self.net_return), "benchmark_return": str(self.benchmark_return),
                "forecast_component_ids": list(self.forecast_component_ids)}
        return body | ({"attribution_hash": self.attribution_hash} if include_hash else {})


class StrategyRegistry:
    """Strategy lifecycle is separate from model lifecycle and action authorization."""

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyDefinition] = {}
        self._transitions: list[StrategyTransition] = []

    @property
    def strategies(self) -> Mapping[str, StrategyDefinition]:
        return MappingProxyType(self._strategies)

    def register(self, strategy: StrategyDefinition) -> None:
        if strategy.status is not StrategyStatus.RESEARCH:
            raise InvariantViolation("STRATEGY_REGISTRATION_MUST_START_IN_RESEARCH")
        previous = self._strategies.get(strategy.key)
        if previous is not None and previous != strategy:
            raise InvariantViolation("STRATEGY_DEFINITION_CONFLICT")
        self._strategies[strategy.key] = strategy

    def transition(self, strategy_key: str, to_status: StrategyStatus, *, effective_at: datetime,
                   reason: str, evidence_ids: tuple[str, ...]) -> StrategyDefinition:
        try:
            current = self._strategies[strategy_key]
        except KeyError:
            raise InvariantViolation("STRATEGY_NOT_REGISTERED") from None
        if not isinstance(to_status, StrategyStatus) or to_status not in _TRANSITIONS[current.status]:
            raise InvariantViolation("STRATEGY_LIFECYCLE_TRANSITION_INVALID")
        transition = StrategyTransition(strategy_key, current.status, to_status, effective_at, reason, evidence_ids)
        previous = [item for item in self._transitions if item.strategy_key == strategy_key]
        if previous and transition.effective_at < previous[-1].effective_at:
            raise InvariantViolation("STRATEGY_TRANSITION_TIME_REVERSED")
        self._strategies[strategy_key] = replace(current, status=to_status)
        self._transitions.append(transition)
        return self._strategies[strategy_key]

    def status_at(self, strategy_key: str, *, at: datetime) -> StrategyStatus:
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise InvariantViolation("STRATEGY_AUTHORIZATION_TIME_NOT_AWARE")
        if strategy_key not in self._strategies:
            raise InvariantViolation("STRATEGY_NOT_REGISTERED")
        status, instant = StrategyStatus.RESEARCH, at.astimezone(timezone.utc)
        for item in self._transitions:
            if item.strategy_key == strategy_key and item.effective_at <= instant:
                if item.from_status is not status:
                    raise InvariantViolation("STRATEGY_LIFECYCLE_HISTORY_INVALID")
                status = item.to_status
        return status

    def authorize(self, strategy_key: str, runtime_mode: StrategyRuntimeMode, *, at: datetime,
                  live_trading_enabled: bool = False) -> StrategyAuthorization:
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise InvariantViolation("STRATEGY_AUTHORIZATION_TIME_NOT_AWARE")
        try:
            strategy = self._strategies[strategy_key]
        except KeyError:
            raise InvariantViolation("STRATEGY_NOT_REGISTERED") from None
        instant = at.astimezone(timezone.utc)
        if not isinstance(runtime_mode, StrategyRuntimeMode) or runtime_mode not in strategy.allowed_runtime_modes:
            raise InvariantViolation("STRATEGY_RUNTIME_MODE_NOT_ALLOWED")
        expected = {
            StrategyRuntimeMode.RESEARCH: StrategyStatus.RESEARCH,
            StrategyRuntimeMode.PAPER: StrategyStatus.PAPER,
            StrategyRuntimeMode.SHADOW: StrategyStatus.SHADOW,
            StrategyRuntimeMode.LIVE: StrategyStatus.LIVE,
        }[runtime_mode]
        if self.status_at(strategy_key, at=instant) is not expected or not strategy.effective_from <= instant < strategy.effective_to:
            raise InvariantViolation("STRATEGY_NOT_ACTIVE_FOR_RUNTIME")
        if runtime_mode is StrategyRuntimeMode.LIVE and not live_trading_enabled:
            raise InvariantViolation("STRATEGY_LIVE_RUNTIME_DISABLED")
        body = {"strategy_key": strategy_key, "runtime_mode": runtime_mode.value,
                "registry_hash": self.registry_hash, "authorized_at": instant.isoformat()}
        return StrategyAuthorization(strategy_key, runtime_mode, self.registry_hash, instant.isoformat(),
                                     digest(canonical(body)))

    def require_authorization(self, authorization: StrategyAuthorization, *, strategy_key: str,
                              runtime_mode: StrategyRuntimeMode, at: datetime,
                              live_trading_enabled: bool = False) -> None:
        """Verify that a caller holds an unmodified, current strategy authorization."""
        if not isinstance(authorization, StrategyAuthorization):
            raise InvariantViolation("STRATEGY_AUTHORIZATION_MISSING")
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise InvariantViolation("STRATEGY_AUTHORIZATION_TIME_NOT_AWARE")
        try:
            authorized_at = datetime.fromisoformat(authorization.authorized_at)
        except (TypeError, ValueError):
            raise InvariantViolation("STRATEGY_AUTHORIZATION_INVALID") from None
        if (not isinstance(authorization.strategy_key, str) or
                not isinstance(authorization.runtime_mode, StrategyRuntimeMode) or
                not isinstance(authorization.registry_hash, str) or
                not isinstance(authorization.authorization_hash, str) or
                authorized_at.tzinfo is None or authorized_at.utcoffset() is None or
                authorized_at.astimezone(timezone.utc).isoformat() != authorization.authorized_at):
            raise InvariantViolation("STRATEGY_AUTHORIZATION_INVALID")
        body = {"strategy_key": authorization.strategy_key,
                "runtime_mode": authorization.runtime_mode.value,
                "registry_hash": authorization.registry_hash,
                "authorized_at": authorization.authorized_at}
        if (authorization.authorization_hash != digest(canonical(body)) or
                authorization.registry_hash != self.registry_hash or
                authorization.strategy_key != strategy_key or authorization.runtime_mode is not runtime_mode or
                authorized_at > at):
            raise InvariantViolation("STRATEGY_AUTHORIZATION_INVALID")
        self.authorize(strategy_key, runtime_mode, at=at,
                       live_trading_enabled=live_trading_enabled)

    def record_attribution(self, store: ImmutableDatasetStore, attribution: StrategyAttribution) -> str:
        if attribution.strategy_key not in self._strategies:
            raise InvariantViolation("STRATEGY_NOT_REGISTERED")
        return store.catalog("strategy-attribution", attribution.payload())

    @property
    def registry_hash(self) -> str:
        return digest(canonical(self._body()))

    def _body(self) -> dict[str, object]:
        return {"strategies": [self._strategies[key].payload() for key in sorted(self._strategies)],
                "transitions": [item.payload() for item in self._transitions]}

    def payload(self) -> dict[str, object]:
        return self._body() | {"registry_hash": self.registry_hash}

    def publish(self, store: ImmutableDatasetStore) -> str:
        return store.catalog("strategy-registry", self.payload())
