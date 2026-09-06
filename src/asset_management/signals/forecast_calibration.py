"""Time-ordered calibration from Signal values to forecast-return components."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest, utc
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.domain.horizon import SignalValidity

from .models import SignalSnapshot


def _decimal(value: Decimal, reason: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantViolation(reason)
    return value


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _rmse(values: Sequence[Decimal]) -> Decimal:
    return _mean([value * value for value in values]).sqrt()


def _solve(matrix: list[list[Decimal]], target: list[Decimal]) -> list[Decimal]:
    size = len(matrix)
    augmented = [row[:] + [target[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = next((row for row in range(column, size) if augmented[row][column] != 0), None)
        if pivot is None:
            raise DataQualityError("FORECAST_CALIBRATION_DESIGN_SINGULAR")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row != column:
                factor = augmented[row][column]
                augmented[row] = [value - factor * pivot_value
                                  for value, pivot_value in zip(augmented[row], augmented[column])]
    return [row[-1] for row in augmented]


def _fit_polynomial(rows: Sequence[tuple[Decimal, Decimal]], degree: int):
    width = degree + 1
    matrix = [[sum((x ** (left + right) for x, _, *_ in rows), Decimal(0))
               for right in range(width)] for left in range(width)]
    target = [sum(((x ** power) * y for x, y, *_ in rows), Decimal(0)) for power in range(width)]
    coefficients = _solve(matrix, target)
    return lambda value: sum((coefficient * value ** power
                              for power, coefficient in enumerate(coefficients)), Decimal(0))


def _fit_monotonic(rows: Sequence[tuple[Decimal, Decimal]]):
    """Fit a deterministic pool-adjacent-violators step function."""
    blocks: list[list[Decimal]] = []
    for x, y, *_ in sorted(rows):
        blocks.append([x, x, y, Decimal(1)])
        while len(blocks) > 1 and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]:
            right, left = blocks.pop(), blocks.pop()
            blocks.append([left[0], right[1], left[2] + right[2], left[3] + right[3]])

    def predict(value: Decimal) -> Decimal:
        for lower, upper, total, count in blocks:
            if value <= upper:
                return total / count
        return blocks[-1][2] / blocks[-1][3]
    return predict


def _snapshot_values(snapshot: SignalSnapshot, universe: tuple[str, ...]) -> dict[str, Decimal]:
    if set(snapshot.values) != set(universe):
        raise InvariantViolation("FORECAST_CALIBRATION_UNIVERSE_PIT_INVALID")
    try:
        values = {item: Decimal(value) if value is not None else None
                  for item, value in snapshot.values.items()}
    except Exception:
        raise InvariantViolation("FORECAST_CALIBRATION_SIGNAL_VALUE_INVALID") from None
    if any(value is None or not value.is_finite() for value in values.values()):
        raise InvariantViolation("FORECAST_CALIBRATION_COVERAGE_INSUFFICIENT")
    return values  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """Completed Signal outcome from one historical, point-in-time cross-section."""

    snapshot: SignalSnapshot
    forward_returns: Mapping[str, Decimal]
    outcome_available_at: datetime
    regime: str
    bucket_labels: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, SignalSnapshot) or not isinstance(self.regime, str) or not self.regime.strip():
            raise InvariantViolation("FORECAST_CALIBRATION_SAMPLE_INVALID")
        available = _aware(self.outcome_available_at, "FORECAST_CALIBRATION_TIME_NOT_AWARE")
        values = dict(self.snapshot.values)
        if set(self.forward_returns) != set(values) or set(self.bucket_labels) != set(values):
            raise InvariantViolation("FORECAST_CALIBRATION_UNIVERSE_PIT_INVALID")
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in self.forward_returns.values()) or any(
                not isinstance(value, str) or not value.strip() for value in self.bucket_labels.values()):
            raise InvariantViolation("FORECAST_CALIBRATION_SAMPLE_INVALID")
        object.__setattr__(self, "outcome_available_at", available)
        object.__setattr__(self, "forward_returns", MappingProxyType(dict(sorted(self.forward_returns.items()))))
        object.__setattr__(self, "bucket_labels", MappingProxyType(dict(sorted(self.bucket_labels.items()))))


def _aware(value: datetime, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ForecastCalibrationConfig:
    minimum_training_samples: int = 8
    minimum_validation_samples: int = 4
    forecast_cost_floor: Decimal = Decimal("0")
    prior_return: Decimal = Decimal("0")
    currency: str = "USD"
    unit: str = "DECIMAL_RETURN"
    model_version: str = "signal-forecast-calibration@1"
    parameter_set_id: str = "signal-forecast-calibration-defaults-v1"

    def __post_init__(self) -> None:
        if (not isinstance(self.minimum_training_samples, int) or self.minimum_training_samples < 3 or
                not isinstance(self.minimum_validation_samples, int) or self.minimum_validation_samples < 2 or
                _decimal(self.forecast_cost_floor, "FORECAST_CALIBRATION_CONFIG_INVALID") < 0 or
                not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isupper() or
                self.unit != "DECIMAL_RETURN" or not isinstance(self.model_version, str) or
                not self.model_version.strip() or not isinstance(self.parameter_set_id, str) or
                not self.parameter_set_id.strip()):
            raise InvariantViolation("FORECAST_CALIBRATION_CONFIG_INVALID")
        _decimal(self.prior_return, "FORECAST_CALIBRATION_CONFIG_INVALID")


@dataclass(frozen=True, slots=True)
class ForecastCalibrationRequest:
    training: tuple[CalibrationSample, ...]
    validation: tuple[CalibrationSample, ...]
    target_snapshot: SignalSnapshot
    target_universe: tuple[str, ...]
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not self.training or not self.validation or not isinstance(self.target_snapshot, SignalSnapshot):
            raise InvariantViolation("FORECAST_CALIBRATION_REQUEST_INVALID")
        evaluated = _aware(self.evaluated_at, "FORECAST_CALIBRATION_TIME_NOT_AWARE")
        universe = tuple(sorted(self.target_universe))
        if not universe or len(set(universe)) != len(universe):
            raise InvariantViolation("FORECAST_CALIBRATION_UNIVERSE_PIT_INVALID")
        target_as_of = _aware(datetime.fromisoformat(self.target_snapshot.as_of),
                              "FORECAST_CALIBRATION_TIME_NOT_AWARE")
        target_cutoff = _aware(datetime.fromisoformat(self.target_snapshot.information_cutoff),
                               "FORECAST_CALIBRATION_TIME_NOT_AWARE")
        if target_cutoff > target_as_of or self.target_snapshot.validity.valid_until <= target_as_of:
            raise InvariantViolation("FORECAST_CALIBRATION_TARGET_INVALID")
        _snapshot_values(self.target_snapshot, universe)
        samples = self.training + self.validation
        for sample in samples:
            sample_as_of = _aware(datetime.fromisoformat(sample.snapshot.as_of),
                                  "FORECAST_CALIBRATION_TIME_NOT_AWARE")
            if (sample.snapshot.signal_id != self.target_snapshot.signal_id or
                    sample.snapshot.signal_version != self.target_snapshot.signal_version or
                    sample.snapshot.universe_manifest_id != self.target_snapshot.universe_manifest_id or
                    sample.snapshot.code_revision != self.target_snapshot.code_revision or
                    sample.snapshot.validity.forecast_horizon != self.target_snapshot.validity.forecast_horizon or
                    sample_as_of >= target_as_of or sample.outcome_available_at > target_cutoff):
                raise InvariantViolation("FORECAST_CALIBRATION_SAMPLE_LINEAGE_INVALID")
        train_end = max(_aware(datetime.fromisoformat(item.snapshot.as_of),
                               "FORECAST_CALIBRATION_TIME_NOT_AWARE") for item in self.training)
        validation_start = min(_aware(datetime.fromisoformat(item.snapshot.as_of),
                                      "FORECAST_CALIBRATION_TIME_NOT_AWARE") for item in self.validation)
        if train_end >= validation_start or any(
                item.outcome_available_at > validation_start for item in self.training):
            raise InvariantViolation("FORECAST_CALIBRATION_TIME_BOUNDARY_INVALID")
        if any(item.outcome_available_at > evaluated for item in self.validation):
            raise InvariantViolation("FORECAST_CALIBRATION_OUTCOME_NOT_AVAILABLE")
        object.__setattr__(self, "evaluated_at", evaluated)
        object.__setattr__(self, "target_universe", universe)


@dataclass(frozen=True, slots=True)
class ForecastCalibrationResult:
    status: str
    reason_code: str
    report: Mapping[str, object] | None
    catalog_id: str | None


class SignalForecastCalibrator:
    """Selects a mapping using time-ordered OOS evidence; it never emits an order."""

    def __init__(self, store: ImmutableDatasetStore) -> None:
        self.store = store

    def calibrate(self, request: ForecastCalibrationRequest, *, config: ForecastCalibrationConfig
                  ) -> ForecastCalibrationResult:
        try:
            train_rows = self._rows(request.training)
            validation_rows = self._rows(request.validation)
            if len(train_rows) < config.minimum_training_samples:
                raise DataQualityError("FORECAST_CALIBRATION_TRAINING_HISTORY_INSUFFICIENT")
            if len(validation_rows) < config.minimum_validation_samples:
                raise DataQualityError("FORECAST_CALIBRATION_VALIDATION_HISTORY_INSUFFICIENT")
            candidates = {
                "LINEAR": _fit_polynomial(train_rows, 1),
                "NONLINEAR_QUADRATIC": _fit_polynomial(train_rows, 2),
                "MONOTONIC": _fit_monotonic(train_rows),
            }
            errors = {
                name: _mean([abs(predict(score) - actual) for score, actual, _, _ in validation_rows])
                for name, predict in candidates.items()
            }
            selected = min(errors, key=lambda name: (errors[name], name))
            predictor = candidates[selected]
            validation_errors = [predictor(score) - actual for score, actual, _, _ in validation_rows]
            uncertainty = _rmse(validation_errors)
            confidence = Decimal(1) / (Decimal(1) + uncertainty * Decimal(100))
            target_values = _snapshot_values(request.target_snapshot, request.target_universe)
            components, floor_count = {}, 0
            for instrument, score in target_values.items():
                raw = predictor(score)
                estimate = raw
                if abs(raw - config.prior_return) <= config.forecast_cost_floor:
                    estimate, floor_count = config.prior_return, floor_count + 1
                components[instrument] = {
                    "semantic_type": "signal_forecast_return_component", "point_estimate": str(estimate),
                    "lower_bound": str(estimate - uncertainty), "upper_bound": str(estimate + uncertainty),
                    "uncertainty": str(uncertainty), "confidence": str(confidence),
                    "currency": config.currency, "unit": config.unit,
                    "horizon": request.target_snapshot.validity.forecast_horizon,
                    "valid_until": request.target_snapshot.validity.valid_until.isoformat(),
                    "model_version": config.model_version,
                }
            stability = self._stability(validation_rows, predictor)
            report = {
                "signal_run_id": request.target_snapshot.signal_run_id,
                "signal_id": request.target_snapshot.signal_id,
                "signal_version": request.target_snapshot.signal_version,
                "universe_manifest_id": request.target_snapshot.universe_manifest_id,
                "as_of": request.target_snapshot.as_of,
                "information_cutoff": request.target_snapshot.information_cutoff,
                "training_signal_run_ids": [sample.snapshot.signal_run_id for sample in request.training],
                "validation_signal_run_ids": [sample.snapshot.signal_run_id for sample in request.validation],
                "selected_mapping": selected,
                "candidate_oos_mae": {name: str(value) for name, value in errors.items()},
                "oos_rmse": str(uncertainty),
                "oos_calibration_curve": self._curve(validation_rows, predictor),
                "regime_bucket_stability": stability,
                "cost_floor": str(config.forecast_cost_floor), "prior_return": str(config.prior_return),
                "cost_floor_shrunk_count": floor_count, "coverage": "1",
                "formula_version": config.model_version, "parameter_set_id": config.parameter_set_id,
                "code_revision": request.target_snapshot.code_revision, "components": components,
            }
            report["forecast_calibration_id"] = digest(canonical(report))
            return ForecastCalibrationResult("READY", "OK", MappingProxyType(report),
                                             self.store.catalog("signal-forecast-components", report))
        except (DataQualityError, InvariantViolation) as exc:
            return ForecastCalibrationResult("ABSTAIN", str(exc), None, None)

    @staticmethod
    def _rows(samples: Sequence[CalibrationSample]) -> list[tuple[Decimal, Decimal, str, str]]:
        rows = []
        for sample in samples:
            universe = tuple(sorted(sample.snapshot.values))
            values = _snapshot_values(sample.snapshot, universe)
            rows.extend((values[item], sample.forward_returns[item], sample.regime, sample.bucket_labels[item])
                        for item in universe)
        return rows

    @staticmethod
    def _curve(rows: Sequence[tuple[Decimal, Decimal, str, str]], predictor) -> dict[str, object]:
        ordered = sorted(rows, key=lambda item: item[0])
        groups = [ordered[index * len(ordered) // 5:(index + 1) * len(ordered) // 5]
                  for index in range(5)]
        return {f"Q{index + 1}": {"mean_signal": str(_mean([item[0] for item in group])),
                                  "mean_realized_return": str(_mean([item[1] for item in group])),
                                  "mean_forecast": str(_mean([predictor(item[0]) for item in group]))}
                for index, group in enumerate(groups) if group}

    @staticmethod
    def _stability(rows: Sequence[tuple[Decimal, Decimal, str, str]], predictor) -> dict[str, object]:
        groups: dict[str, list[tuple[Decimal, Decimal]]] = defaultdict(list)
        for score, actual, regime, bucket in rows:
            groups[f"regime:{regime}"].append((score, actual))
            groups[f"bucket:{bucket}"].append((score, actual))
        return {name: {"observation_count": len(values),
                       "mae": str(_mean([abs(predictor(score) - actual) for score, actual in values]))}
                for name, values in sorted(groups.items())}
