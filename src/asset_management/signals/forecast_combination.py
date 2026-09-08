"""Diversified, cost-aware combination of calibrated Signal forecast overlays."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
import re
from types import MappingProxyType
from typing import Mapping, Sequence

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest, utc
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.domain.horizon import SignalValidity


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


def _is_psd(matrix: Sequence[Sequence[Decimal]]) -> bool:
    """Exact Schur-complement test without rounded square roots.

    Decimal inputs are finite rationals. Using their exact fractions avoids
    rejecting singular positive-semidefinite matrices due to sqrt roundoff.
    """
    size = len(matrix)
    work = [[Fraction(value) for value in row] for row in matrix]
    for pivot in range(size):
        diagonal = work[pivot][pivot]
        if diagonal < 0:
            return False
        if diagonal == 0:
            if any(work[row][pivot] != 0 for row in range(pivot + 1, size)):
                return False
            continue
        for row in range(pivot + 1, size):
            for column in range(row, size):
                value = work[row][column] - work[row][pivot] * work[column][pivot] / diagonal
                work[row][column] = work[column][row] = value
    return True


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
    """A calibrated forecast overlay with complete SignalValidity and OOS lineage."""

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
    validity: SignalValidity
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
                not isinstance(self.validity, SignalValidity)):
            raise InvariantViolation("FORECAST_COMBINATION_SOURCE_INVALID")
        as_of = _aware(self.as_of, "FORECAST_COMBINATION_TIME_NOT_AWARE")
        cutoff = _aware(self.information_cutoff, "FORECAST_COMBINATION_TIME_NOT_AWARE")
        evidence = _aware(self.oos_evidence_available_at, "FORECAST_COMBINATION_TIME_NOT_AWARE")
        if cutoff > as_of or self.validity.valid_until <= as_of:
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
                            ("oos_evidence_available_at", evidence),
                            ("point_estimates", MappingProxyType(estimates))):
            object.__setattr__(self, name, value)

    @property
    def horizon(self) -> int:
        return self.validity.forecast_horizon

    @property
    def valid_until(self) -> datetime:
        return self.validity.valid_until


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
                    source.unit != first.unit or source.validity != first.validity or
                    set(source.point_estimates) != universe or
                    source.oos_evidence_available_at > first.information_cutoff or
                    source.oos_evidence_available_at > evaluated or source.as_of > evaluated or
                    source.validity.valid_until <= evaluated):
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
        if not _is_psd(self.covariance):
            raise InvariantViolation("FORECAST_COMBINATION_COVARIANCE_NOT_PSD")
        if not _is_psd(self.correlation):
            raise InvariantViolation("FORECAST_COMBINATION_CORRELATION_NOT_PSD")
        # A frozen dataclass must not retain caller-owned mutable matrices.
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "covariance", tuple(tuple(row) for row in self.covariance))
        object.__setattr__(self, "correlation", tuple(tuple(row) for row in self.correlation))
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
            validity_weight = request.sources[0].validity.effective_weight(
                produced_at=request.sources[0].as_of, evaluated_at=request.evaluated_at)
            decay_application = {
                "contract_version": "forecast-validity-at-evaluation/v1",
                "stage": "FORECAST_VALIDITY", "produced_at": utc(request.sources[0].as_of),
                "applied_at": utc(request.evaluated_at), "weight": str(validity_weight),
            }
            components = {}
            for instrument in request.sources[0].point_estimates:
                gross = sum((weight * source.point_estimates[instrument]
                             for weight, source in zip(weights, request.sources)), Decimal(0))
                # Discount the forecast once, not normalized weights (which
                # would cancel a shared decay). Costs and uncertainty are not
                # reduced merely because the information is older.
                gross *= validity_weight
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
                    "signal_validity": request.sources[0].validity.payload(),
                    "forecast_validity_decay": dict(decay_application),
                    "evaluated_at": utc(request.evaluated_at),
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
                "signal_validity": request.sources[0].validity.payload(),
                "forecast_validity_decay": dict(decay_application),
                "evaluated_at": utc(request.evaluated_at),
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
        cap = parameters.max_forecast_weight
        if cap * Decimal(len(raw)) < Decimal(1):
            raise DataQualityError("FORECAST_COMBINATION_WEIGHT_CAP_INFEASIBLE")
        weights = [Decimal(0) for _ in raw]
        remaining = Decimal(1)
        eligible = set(range(len(raw)))
        while eligible:
            base = sum((raw[index] for index in eligible), Decimal(0))
            proposed = {
                index: (remaining / Decimal(len(eligible)) if base == 0
                        else remaining * raw[index] / base)
                for index in eligible
            }
            violating = {index for index, value in proposed.items() if value > cap}
            if not violating:
                for index, value in proposed.items():
                    weights[index] = value
                break
            for index in sorted(violating):
                weights[index] = cap
                remaining -= cap
                eligible.remove(index)
            if remaining < 0:
                raise DataQualityError("FORECAST_COMBINATION_WEIGHT_CAP_INFEASIBLE")
        residual = Decimal(1) - sum(weights, Decimal(0))
        if residual:
            candidates = [index for index, value in enumerate(weights) if value + residual <= cap]
            if not candidates:
                raise DataQualityError("FORECAST_COMBINATION_WEIGHT_CAP_INFEASIBLE")
            weights[candidates[-1]] += residual
        if any(value < 0 or value > cap for value in weights) or sum(weights, Decimal(0)) != Decimal(1):
            raise DataQualityError("FORECAST_COMBINATION_WEIGHT_CAP_INFEASIBLE")
        return tuple(weights)
