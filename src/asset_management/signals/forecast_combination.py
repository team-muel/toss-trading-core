"""Diversified, cost-aware combination of calibrated Signal forecast overlays."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Mapping, Sequence

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest, utc
from asset_management.domain.errors import DataQualityError, InvariantViolation


_HASH = re.compile(r"[0-9a-f]{64}")


def _decimal(value: Decimal, reason: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantViolation(reason)
    return value


def _aware(value: datetime, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


def _sqrt(value: Decimal) -> Decimal:
    if value < 0:
        raise DataQualityError("FORECAST_COMBINATION_COVARIANCE_INVALID")
    return value.sqrt()


@dataclass(frozen=True, slots=True)
class ForecastCombinationParameters:
    combination_id: str
    version: str
    max_forecast_weight: Decimal
    cost_penalty: Decimal
    formula_version: str
    parameter_set_id: str

    def __post_init__(self) -> None:
        if (not isinstance(self.combination_id, str) or not self.combination_id.strip() or
                not isinstance(self.version, str) or not self.version.strip() or
                not Decimal(0) < _decimal(self.max_forecast_weight, "FORECAST_COMBINATION_PARAMETERS_INVALID") <= Decimal(1) or
                _decimal(self.cost_penalty, "FORECAST_COMBINATION_PARAMETERS_INVALID") < 0 or
                not isinstance(self.formula_version, str) or not self.formula_version.strip() or
                not isinstance(self.parameter_set_id, str) or not self.parameter_set_id.strip()):
            raise InvariantViolation("FORECAST_COMBINATION_PARAMETERS_INVALID")

    @property
    def key(self) -> str:
        return f"{self.combination_id}@{self.version}"

    def payload(self) -> dict[str, str]:
        return {
            "combination_id": self.combination_id, "version": self.version,
            "max_forecast_weight": str(self.max_forecast_weight), "cost_penalty": str(self.cost_penalty),
            "formula_version": self.formula_version, "parameter_set_id": self.parameter_set_id,
        }


class ForecastCombinationRegistry:
    """Versioned, immutable parameter registry for OOS combination weights."""

    def __init__(self, parameters: Sequence[ForecastCombinationParameters] = ()) -> None:
        self._items: dict[str, ForecastCombinationParameters] = {}
        for item in parameters:
            self.register(item)

    def register(self, parameters: ForecastCombinationParameters) -> None:
        current = self._items.get(parameters.key)
        if current is not None and current != parameters:
            raise InvariantViolation("FORECAST_COMBINATION_PARAMETER_CONFLICT")
        self._items[parameters.key] = parameters

    def get(self, combination_id: str, version: str) -> ForecastCombinationParameters:
        try:
            return self._items[f"{combination_id}@{version}"]
        except KeyError:
            raise InvariantViolation("FORECAST_COMBINATION_PARAMETERS_UNKNOWN") from None


@dataclass(frozen=True, slots=True)
class ForecastSource:
    """A calibrated forecast overlay with neutralization and OOS evidence lineage."""

    forecast_calibration_id: str
    signal_run_id: str
    neutralization_id: str
    signal_id: str
    as_of: datetime
    information_cutoff: datetime
    oos_evidence_available_at: datetime
    universe_manifest_id: str
    currency: str
    unit: str
    horizon: int
    valid_until: datetime
    point_estimates: Mapping[str, Decimal]
    uncertainty: Decimal
    confidence: Decimal
    incremental_ic: Decimal
    stability: Decimal
    coverage: Decimal
    regime_sensitivity: Decimal
    turnover: Decimal
    implementation_cost: Decimal

    def __post_init__(self) -> None:
        for value in (self.forecast_calibration_id, self.signal_run_id, self.neutralization_id,
                      self.universe_manifest_id):
            if not isinstance(value, str) or not _HASH.fullmatch(value):
                raise InvariantViolation("FORECAST_COMBINATION_LINEAGE_INVALID")
        if (not isinstance(self.signal_id, str) or not self.signal_id.strip() or
                self.currency != "USD" or self.unit != "DECIMAL_RETURN" or
                self.horizon not in {21, 63, 126, 252}):
            raise InvariantViolation("FORECAST_COMBINATION_SOURCE_INVALID")
        as_of = _aware(self.as_of, "FORECAST_COMBINATION_TIME_NOT_AWARE")
        cutoff = _aware(self.information_cutoff, "FORECAST_COMBINATION_TIME_NOT_AWARE")
        evidence = _aware(self.oos_evidence_available_at, "FORECAST_COMBINATION_TIME_NOT_AWARE")
        valid_until = _aware(self.valid_until, "FORECAST_COMBINATION_TIME_NOT_AWARE")
        if cutoff > as_of or valid_until <= as_of:
            raise InvariantViolation("FORECAST_COMBINATION_SOURCE_INVALID")
        estimates = dict(sorted(self.point_estimates.items()))
        if not estimates or any(not isinstance(key, str) or not key.strip() or
                                not isinstance(value, Decimal) or not value.is_finite()
                                for key, value in estimates.items()):
            raise InvariantViolation("FORECAST_COMBINATION_SOURCE_INVALID")
        if (not Decimal(0) <= _decimal(self.confidence, "FORECAST_COMBINATION_SOURCE_INVALID") <= Decimal(1) or
                _decimal(self.uncertainty, "FORECAST_COMBINATION_SOURCE_INVALID") < 0 or
                not Decimal(0) <= _decimal(self.stability, "FORECAST_COMBINATION_SOURCE_INVALID") <= Decimal(1) or
                not Decimal(0) < _decimal(self.coverage, "FORECAST_COMBINATION_SOURCE_INVALID") <= Decimal(1) or
                not Decimal(0) <= _decimal(self.regime_sensitivity, "FORECAST_COMBINATION_SOURCE_INVALID") <= Decimal(1) or
                not Decimal(0) <= _decimal(self.turnover, "FORECAST_COMBINATION_SOURCE_INVALID") <= Decimal(1) or
                _decimal(self.implementation_cost, "FORECAST_COMBINATION_SOURCE_INVALID") < 0):
            raise InvariantViolation("FORECAST_COMBINATION_SOURCE_INVALID")
        _decimal(self.incremental_ic, "FORECAST_COMBINATION_SOURCE_INVALID")
        for name, value in (("as_of", as_of), ("information_cutoff", cutoff),
                            ("oos_evidence_available_at", evidence), ("valid_until", valid_until),
                            ("point_estimates", MappingProxyType(estimates))):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class ForecastCombinationRequest:
    sources: tuple[ForecastSource, ...]
    covariance: tuple[tuple[Decimal, ...], ...]
    correlation: tuple[tuple[Decimal, ...], ...]
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if len(self.sources) < 2:
            raise InvariantViolation("FORECAST_COMBINATION_SOURCES_INSUFFICIENT")
        evaluated = _aware(self.evaluated_at, "FORECAST_COMBINATION_TIME_NOT_AWARE")
        first, size = self.sources[0], len(self.sources)
        if len({source.signal_id for source in self.sources}) != size:
            raise InvariantViolation("FORECAST_COMBINATION_SIGNAL_IDENTITY_CONFLICT")
        if len(self.covariance) != size or len(self.correlation) != size:
            raise InvariantViolation("FORECAST_COMBINATION_MATRIX_INVALID")
        universe = set(first.point_estimates)
        for source in self.sources:
            if (source.as_of != first.as_of or source.information_cutoff != first.information_cutoff or
                    source.universe_manifest_id != first.universe_manifest_id or source.currency != first.currency or
                    source.unit != first.unit or source.horizon != first.horizon or
                    source.valid_until != first.valid_until or set(source.point_estimates) != universe or
                    source.oos_evidence_available_at > first.information_cutoff or
                    source.oos_evidence_available_at > evaluated):
                raise InvariantViolation("FORECAST_COMBINATION_LINEAGE_OR_TIME_INVALID")
        for matrix, diagonal in ((self.covariance, False), (self.correlation, True)):
            if any(len(row) != size for row in matrix):
                raise InvariantViolation("FORECAST_COMBINATION_MATRIX_INVALID")
            for row in range(size):
                for column in range(size):
                    value = matrix[row][column]
                    if (not isinstance(value, Decimal) or not value.is_finite() or value != matrix[column][row] or
                            (diagonal and ((row == column and value != 1) or
                                           (row != column and not Decimal(-1) <= value <= Decimal(1)))) or
                            (not diagonal and row == column and value < 0)):
                        raise InvariantViolation("FORECAST_COMBINATION_MATRIX_INVALID")
        object.__setattr__(self, "evaluated_at", evaluated)


@dataclass(frozen=True, slots=True)
class ForecastCombinationResult:
    status: str
    reason_code: str
    report: Mapping[str, object] | None
    catalog_id: str | None


class ForecastCombiner:
    """Combines forecast overlays only; pricing baselines stay outside this module."""

    def __init__(self, store: ImmutableDatasetStore, registry: ForecastCombinationRegistry) -> None:
        self.store, self.registry = store, registry

    def combine(self, request: ForecastCombinationRequest, *, combination_id: str,
                version: str) -> ForecastCombinationResult:
        try:
            parameters = self.registry.get(combination_id, version)
            weights = self._weights(request.sources, request.correlation, parameters)
            combined_uncertainty = _sqrt(sum(
                weights[left] * request.covariance[left][right] * weights[right]
                for left in range(len(weights)) for right in range(len(weights))
            ))
            independent_denom = sum(
                weights[left] * request.correlation[left][right] * weights[right]
                for left in range(len(weights)) for right in range(len(weights))
            )
            if independent_denom <= 0:
                raise DataQualityError("FORECAST_COMBINATION_CORRELATION_INVALID")
            effective = Decimal(1) / independent_denom
            expected_cost = sum((weight * source.implementation_cost
                                 for weight, source in zip(weights, request.sources)), Decimal(0))
            components = {}
            for instrument in request.sources[0].point_estimates:
                gross = sum((weight * source.point_estimates[instrument]
                             for weight, source in zip(weights, request.sources)), Decimal(0))
                net = gross - expected_cost
                components[instrument] = {
                    "semantic_type": "combined_signal_forecast_component",
                    "gross_point_estimate": str(gross), "net_point_estimate": str(net),
                    "uncertainty": str(combined_uncertainty),
                    "lower_bound": str(net - combined_uncertainty),
                    "upper_bound": str(net + combined_uncertainty),
                    "currency": request.sources[0].currency, "unit": request.sources[0].unit,
                    "horizon": request.sources[0].horizon,
                    "valid_until": request.sources[0].valid_until.isoformat(),
                }
            contribution = {
                source.signal_id: {
                    "weight": str(weight), "incremental_ic": str(source.incremental_ic),
                    "confidence": str(source.confidence), "stability": str(source.stability),
                    "coverage": str(source.coverage), "regime_sensitivity": str(source.regime_sensitivity),
                    "turnover": str(source.turnover), "implementation_cost": str(source.implementation_cost),
                    "forecast_calibration_id": source.forecast_calibration_id,
                    "neutralization_id": source.neutralization_id,
                } for weight, source in zip(weights, request.sources)
            }
            report = {
                "combined_forecast_id": "", "semantic_type": "combined_signal_forecast_component",
                "source_forecast_calibration_ids": [source.forecast_calibration_id for source in request.sources],
                "neutralization_lineage_ids": [source.neutralization_id for source in request.sources],
                "universe_manifest_id": request.sources[0].universe_manifest_id,
                "as_of": utc(request.sources[0].as_of), "information_cutoff": utc(request.sources[0].information_cutoff),
                "parameter_registry_key": parameters.key, "formula_version": parameters.formula_version,
                "parameter_set_id": parameters.parameter_set_id, "contributions": contribution,
                "effective_independent_forecasts": str(effective), "combined_uncertainty": str(combined_uncertainty),
                "expected_implementation_cost": str(expected_cost), "components": components,
            }
            report["combined_forecast_id"] = digest(canonical(report))
            return ForecastCombinationResult("READY", "OK", MappingProxyType(report),
                                             self.store.catalog("combined-forecast-components", report))
        except (DataQualityError, InvariantViolation) as exc:
            return ForecastCombinationResult("ABSTAIN", str(exc), None, None)

    @staticmethod
    def _weights(sources: Sequence[ForecastSource], correlation: Sequence[Sequence[Decimal]],
                 parameters: ForecastCombinationParameters) -> tuple[Decimal, ...]:
        raw = []
        for index, source in enumerate(sources):
            information = (max(source.incremental_ic, Decimal(0)) * source.confidence * source.stability *
                           source.coverage * (Decimal(1) - source.regime_sensitivity))
            cost = Decimal(1) + parameters.cost_penalty * (source.turnover + source.implementation_cost)
            overlap = sum((abs(correlation[index][other]) for other in range(len(sources)) if other != index),
                          Decimal(0)) / Decimal(len(sources) - 1)
            raw.append(information / cost / (Decimal(1) + overlap))
        total = sum(raw, Decimal(0))
        if total <= 0:
            raise DataQualityError("FORECAST_COMBINATION_INFORMATION_INSUFFICIENT")
        weights = [value / total for value in raw]
        if any(value > parameters.max_forecast_weight for value in weights):
            capped = [min(value, parameters.max_forecast_weight) for value in weights]
            remainder = Decimal(1) - sum(capped, Decimal(0))
            eligible = [index for index, value in enumerate(weights) if value < parameters.max_forecast_weight]
            if remainder > 0 and not eligible:
                raise DataQualityError("FORECAST_COMBINATION_WEIGHT_CAP_INFEASIBLE")
            base = sum((weights[index] for index in eligible), Decimal(0))
            for index in eligible:
                capped[index] += remainder * weights[index] / base
            weights = capped
        if any(value > parameters.max_forecast_weight for value in weights):
            raise DataQualityError("FORECAST_COMBINATION_WEIGHT_CAP_INFEASIBLE")
        weights[-1] = Decimal(1) - sum(weights[:-1], Decimal(0))
        return tuple(weights)
