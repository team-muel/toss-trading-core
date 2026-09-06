"""Immutable investor mandate, benchmark, and optimizer-risk authority contracts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation


class MandateObjective(StrEnum):
    ABSOLUTE_WEALTH = "ABSOLUTE_WEALTH"
    BENCHMARK_RELATIVE = "BENCHMARK_RELATIVE"
    MIXED = "MIXED"


class WealthConvention(StrEnum):
    NOMINAL = "NOMINAL"
    REAL = "REAL"


def _decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantViolation("MANDATE_NUMERIC_VALUE_INVALID")
    return value


def _ids(values: tuple[str, ...], reason: str) -> tuple[str, ...]:
    if not values or len(values) != len(set(values)) or any(not isinstance(value, str) or not value.strip() for value in values):
        raise InvariantViolation(reason)
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class BenchmarkDefinition:
    benchmark_id: str
    version: str
    name: str
    total_return: bool
    currency: str
    rebalance_horizon: int
    universe_key: str
    effective_from: datetime
    effective_to: datetime

    def __post_init__(self) -> None:
        if (any(not isinstance(value, str) or not value.strip() for value in
                (self.benchmark_id, self.version, self.name, self.currency, self.universe_key)) or
                type(self.total_return) is not bool or self.currency != "USD" or
                self.rebalance_horizon not in {21, 63, 126, 252} or
                not isinstance(self.effective_from, datetime) or not isinstance(self.effective_to, datetime) or
                self.effective_from.tzinfo is None or self.effective_from.utcoffset() is None or
                self.effective_to.tzinfo is None or self.effective_to.utcoffset() is None):
            raise InvariantViolation("BENCHMARK_DEFINITION_INVALID")
        start, end = self.effective_from.astimezone(timezone.utc), self.effective_to.astimezone(timezone.utc)
        if end <= start:
            raise InvariantViolation("BENCHMARK_EFFECTIVE_WINDOW_INVALID")
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_to", end)

    @property
    def key(self) -> str:
        return f"{self.benchmark_id}@{self.version}"

    def payload(self) -> dict[str, object]:
        return {"benchmark_id": self.benchmark_id, "version": self.version, "name": self.name,
                "total_return": self.total_return, "currency": self.currency,
                "rebalance_horizon": self.rebalance_horizon, "universe_key": self.universe_key,
                "effective_from": self.effective_from.isoformat(), "effective_to": self.effective_to.isoformat()}


@dataclass(frozen=True, slots=True)
class RiskPreference:
    max_drawdown: Decimal
    max_stress_loss: Decimal
    liquidity_reserve: Decimal
    strategic_risk_budget: Decimal
    active_risk_budget: Decimal
    tracking_error_budget: Decimal
    concentration_policy_key: str
    turnover_policy_key: str
    tax_policy_key: str
    liquidity_policy_key: str
    risk_aversion_min: Decimal
    risk_aversion_max: Decimal
    active_risk_aversion_min: Decimal
    active_risk_aversion_max: Decimal
    authority_source: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (any(not isinstance(value, str) or not value.strip() for value in
                (self.concentration_policy_key, self.turnover_policy_key, self.tax_policy_key,
                 self.liquidity_policy_key, self.authority_source)) or
                any(not Decimal(0) <= _decimal(value) <= Decimal(1) for value in
                    (self.max_drawdown, self.max_stress_loss, self.liquidity_reserve,
                     self.strategic_risk_budget, self.active_risk_budget, self.tracking_error_budget)) or
                not Decimal(0) <= _decimal(self.risk_aversion_min) <= _decimal(self.risk_aversion_max) or
                not Decimal(0) <= _decimal(self.active_risk_aversion_min) <= _decimal(self.active_risk_aversion_max)):
            raise InvariantViolation("RISK_PREFERENCE_INVALID")
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "RISK_PREFERENCE_EVIDENCE_INVALID"))

    def payload(self) -> dict[str, object]:
        return {"max_drawdown": str(self.max_drawdown), "max_stress_loss": str(self.max_stress_loss),
                "liquidity_reserve": str(self.liquidity_reserve), "strategic_risk_budget": str(self.strategic_risk_budget),
                "active_risk_budget": str(self.active_risk_budget), "tracking_error_budget": str(self.tracking_error_budget),
                "concentration_policy_key": self.concentration_policy_key, "turnover_policy_key": self.turnover_policy_key,
                "tax_policy_key": self.tax_policy_key, "liquidity_policy_key": self.liquidity_policy_key,
                "risk_aversion_min": str(self.risk_aversion_min), "risk_aversion_max": str(self.risk_aversion_max),
                "active_risk_aversion_min": str(self.active_risk_aversion_min),
                "active_risk_aversion_max": str(self.active_risk_aversion_max),
                "authority_source": self.authority_source, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True, slots=True)
class InvestorMandate:
    mandate_id: str
    version: str
    objective: MandateObjective
    base_currency: str
    reporting_currency: str
    wealth_convention: WealthConvention
    investment_horizon: int
    liquidity_horizon: int
    rebalance_horizon: int
    primary_benchmark_key: str
    secondary_benchmark_key: str | None
    cash_benchmark_key: str
    risk_preference: RiskPreference
    effective_from: datetime
    effective_to: datetime

    def __post_init__(self) -> None:
        if (any(not isinstance(value, str) or not value.strip() for value in
                (self.mandate_id, self.version, self.base_currency, self.reporting_currency,
                 self.primary_benchmark_key, self.cash_benchmark_key)) or
                self.secondary_benchmark_key is not None and (not isinstance(self.secondary_benchmark_key, str) or not self.secondary_benchmark_key.strip()) or
                not isinstance(self.objective, MandateObjective) or not isinstance(self.wealth_convention, WealthConvention) or
                self.base_currency != "USD" or self.reporting_currency not in {"USD", "KRW"} or
                not isinstance(self.risk_preference, RiskPreference) or
                any(value not in {21, 63, 126, 252} for value in (self.investment_horizon, self.liquidity_horizon, self.rebalance_horizon)) or
                self.liquidity_horizon > self.investment_horizon or
                not isinstance(self.effective_from, datetime) or not isinstance(self.effective_to, datetime) or
                self.effective_from.tzinfo is None or self.effective_from.utcoffset() is None or
                self.effective_to.tzinfo is None or self.effective_to.utcoffset() is None):
            raise InvariantViolation("INVESTOR_MANDATE_INVALID")
        start, end = self.effective_from.astimezone(timezone.utc), self.effective_to.astimezone(timezone.utc)
        if end <= start:
            raise InvariantViolation("INVESTOR_MANDATE_EFFECTIVE_WINDOW_INVALID")
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_to", end)

    @property
    def key(self) -> str:
        return f"{self.mandate_id}@{self.version}"

    def payload(self) -> dict[str, object]:
        return {"mandate_id": self.mandate_id, "version": self.version, "objective": self.objective.value,
                "base_currency": self.base_currency, "reporting_currency": self.reporting_currency,
                "wealth_convention": self.wealth_convention.value, "investment_horizon": self.investment_horizon,
                "liquidity_horizon": self.liquidity_horizon, "rebalance_horizon": self.rebalance_horizon,
                "primary_benchmark_key": self.primary_benchmark_key, "secondary_benchmark_key": self.secondary_benchmark_key,
                "cash_benchmark_key": self.cash_benchmark_key, "risk_preference": self.risk_preference.payload(),
                "effective_from": self.effective_from.isoformat(), "effective_to": self.effective_to.isoformat()}


@dataclass(frozen=True, slots=True)
class OptimizerMandateAuthorization:
    mandate_key: str
    primary_benchmark_key: str
    risk_aversion: Decimal
    active_risk_aversion: Decimal
    registry_hash: str
    authorized_at: str
    authorization_hash: str


class InvestorMandateRegistry:
    def __init__(self) -> None:
        self._benchmarks: dict[str, BenchmarkDefinition] = {}
        self._mandates: dict[str, InvestorMandate] = {}

    def register_benchmark(self, benchmark: BenchmarkDefinition) -> None:
        previous = self._benchmarks.get(benchmark.key)
        if previous is not None and previous != benchmark:
            raise InvariantViolation("BENCHMARK_DEFINITION_CONFLICT")
        self._benchmarks[benchmark.key] = benchmark

    def register_mandate(self, mandate: InvestorMandate) -> None:
        for key in (mandate.primary_benchmark_key, mandate.cash_benchmark_key, mandate.secondary_benchmark_key):
            if key is not None and key not in self._benchmarks:
                raise InvariantViolation("MANDATE_BENCHMARK_NOT_REGISTERED")
        previous = self._mandates.get(mandate.key)
        if previous is not None and previous != mandate:
            raise InvariantViolation("INVESTOR_MANDATE_CONFLICT")
        self._mandates[mandate.key] = mandate

    @property
    def registry_hash(self) -> str:
        return digest(canonical(self._body()))

    def _body(self) -> dict[str, object]:
        return {"benchmarks": [self._benchmarks[key].payload() for key in sorted(self._benchmarks)],
                "mandates": [self._mandates[key].payload() for key in sorted(self._mandates)]}

    def payload(self) -> dict[str, object]:
        return self._body() | {"registry_hash": self.registry_hash}

    def authorize_optimizer(self, mandate_key: str, *, risk_aversion: Decimal,
                            active_risk_aversion: Decimal, at: datetime) -> OptimizerMandateAuthorization:
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise InvariantViolation("MANDATE_AUTHORIZATION_TIME_NOT_AWARE")
        try:
            mandate = self._mandates[mandate_key]
        except KeyError:
            raise InvariantViolation("INVESTOR_MANDATE_NOT_REGISTERED") from None
        instant = at.astimezone(timezone.utc)
        benchmark = self._benchmarks[mandate.primary_benchmark_key]
        preference = mandate.risk_preference
        if not mandate.effective_from <= instant < mandate.effective_to or not benchmark.effective_from <= instant < benchmark.effective_to:
            raise InvariantViolation("MANDATE_OR_BENCHMARK_NOT_EFFECTIVE")
        if not preference.risk_aversion_min <= _decimal(risk_aversion) <= preference.risk_aversion_max or not preference.active_risk_aversion_min <= _decimal(active_risk_aversion) <= preference.active_risk_aversion_max:
            raise InvariantViolation("OPTIMIZER_RISK_AVERSION_NOT_AUTHORIZED")
        body = {"mandate_key": mandate_key, "primary_benchmark_key": mandate.primary_benchmark_key,
                "risk_aversion": str(risk_aversion), "active_risk_aversion": str(active_risk_aversion),
                "registry_hash": self.registry_hash, "authorized_at": instant.isoformat()}
        return OptimizerMandateAuthorization(mandate_key, mandate.primary_benchmark_key, risk_aversion,
                                             active_risk_aversion, self.registry_hash, instant.isoformat(), digest(canonical(body)))

    def require_performance_benchmark(self, mandate_key: str, *, benchmark_key: str,
                                      at: datetime) -> BenchmarkDefinition:
        """Return only the precommitted primary benchmark effective at ``at``."""
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise InvariantViolation("MANDATE_AUTHORIZATION_TIME_NOT_AWARE")
        try:
            mandate = self._mandates[mandate_key]
        except KeyError:
            raise InvariantViolation("INVESTOR_MANDATE_NOT_REGISTERED") from None
        if benchmark_key != mandate.primary_benchmark_key:
            raise InvariantViolation("PERFORMANCE_BENCHMARK_NOT_MANDATED")
        benchmark = self._benchmarks[benchmark_key]
        instant = at.astimezone(timezone.utc)
        if not mandate.effective_from <= instant < mandate.effective_to or not benchmark.effective_from <= instant < benchmark.effective_to:
            raise InvariantViolation("MANDATE_OR_BENCHMARK_NOT_EFFECTIVE")
        return benchmark

    def require_optimizer_authorization(self, authorization: OptimizerMandateAuthorization, *, risk_aversion: Decimal,
                                        active_risk_aversion: Decimal, at: datetime) -> None:
        if not isinstance(authorization, OptimizerMandateAuthorization):
            raise InvariantViolation("OPTIMIZER_MANDATE_AUTHORIZATION_MISSING")
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise InvariantViolation("MANDATE_AUTHORIZATION_TIME_NOT_AWARE")
        try:
            authorized_at = datetime.fromisoformat(authorization.authorized_at)
        except (TypeError, ValueError):
            raise InvariantViolation("OPTIMIZER_MANDATE_AUTHORIZATION_INVALID") from None
        body = {"mandate_key": authorization.mandate_key, "primary_benchmark_key": authorization.primary_benchmark_key,
                "risk_aversion": str(authorization.risk_aversion), "active_risk_aversion": str(authorization.active_risk_aversion),
                "registry_hash": authorization.registry_hash, "authorized_at": authorization.authorized_at}
        if (authorized_at.tzinfo is None or authorized_at.utcoffset() is None or
                authorization.authorization_hash != digest(canonical(body)) or authorization.registry_hash != self.registry_hash or
                authorization.risk_aversion != risk_aversion or authorization.active_risk_aversion != active_risk_aversion or
                authorized_at > at):
            raise InvariantViolation("OPTIMIZER_MANDATE_AUTHORIZATION_INVALID")
        self.authorize_optimizer(authorization.mandate_key, risk_aversion=risk_aversion,
                                active_risk_aversion=active_risk_aversion, at=at)

    def publish(self, store: ImmutableDatasetStore) -> str:
        return store.catalog("investor-mandate-registry", self.payload())
