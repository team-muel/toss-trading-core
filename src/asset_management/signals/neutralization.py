"""PIT-only signal neutralization and incremental predictive-power diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest, utc
from asset_management.domain.errors import DataQualityError, InvariantViolation

from .diagnostics import _pearson, _ranks
from .models import SignalSnapshot


_GROUPS = frozenset(("sector", "industry", "size", "liquidity"))


def _decimal(value: Decimal, reason: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantViolation(reason)
    return value


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _signal_values(snapshot: SignalSnapshot, universe: tuple[str, ...]) -> dict[str, Decimal]:
    if set(snapshot.values) != set(universe):
        raise InvariantViolation("NEUTRALIZATION_UNIVERSE_PIT_INVALID")
    try:
        values = {key: Decimal(value) if value is not None else None for key, value in snapshot.values.items()}
    except Exception:
        raise InvariantViolation("NEUTRALIZATION_SIGNAL_VALUE_INVALID") from None
    if any(value is None or not value.is_finite() for value in values.values()):
        raise InvariantViolation("NEUTRALIZATION_SIGNAL_COVERAGE_INSUFFICIENT")
    return values  # type: ignore[return-value]


def _solve(matrix: list[list[Decimal]], target: list[Decimal]) -> list[Decimal]:
    size = len(matrix)
    augmented = [row[:] + [target[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = next((row for row in range(column, size) if augmented[row][column] != 0), None)
        if pivot is None:
            raise DataQualityError("NEUTRALIZATION_DESIGN_SINGULAR")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row != column:
                factor = augmented[row][column]
                augmented[row] = [value - factor * pivot_value
                                  for value, pivot_value in zip(augmented[row], augmented[column])]
    return [row[-1] for row in augmented]


def _residualize(values: Mapping[str, Decimal], controls: list[Mapping[str, Decimal]],
                 universe: tuple[str, ...]) -> dict[str, Decimal]:
    rows = [[Decimal(1)] + [column[item] for column in controls] for item in universe]
    width = len(rows[0])
    gram = [[sum((row[left] * row[right] for row in rows), Decimal(0))
             for right in range(width)] for left in range(width)]
    rhs = [sum((row[column] * values[item] for row, item in zip(rows, universe)), Decimal(0))
           for column in range(width)]
    coefficients = _solve(gram, rhs)
    return {item: values[item] - sum((coefficient * cell for coefficient, cell in zip(coefficients, row)),
                                     Decimal(0)) for item, row in zip(universe, rows)}


@dataclass(frozen=True, slots=True)
class NeutralizationConfig:
    minimum_embargo: timedelta = timedelta(days=1)
    transaction_cost_per_turnover: Decimal = Decimal("0")
    formula_version: str = "signal-neutralization@1"
    parameter_set_id: str = "signal-neutralization-defaults-v1"

    def __post_init__(self) -> None:
        if (not isinstance(self.minimum_embargo, timedelta) or self.minimum_embargo <= timedelta(0) or
                _decimal(self.transaction_cost_per_turnover, "NEUTRALIZATION_CONFIG_INVALID") < 0 or
                not isinstance(self.formula_version, str) or not self.formula_version.strip() or
                not isinstance(self.parameter_set_id, str) or not self.parameter_set_id.strip()):
            raise InvariantViolation("NEUTRALIZATION_CONFIG_INVALID")


@dataclass(frozen=True, slots=True)
class NeutralizationInput:
    candidate: SignalSnapshot
    baselines: tuple[SignalSnapshot, ...]
    as_of: datetime
    information_cutoff: datetime
    embargo_until: datetime
    outcome_available_at: datetime
    universe: tuple[str, ...]
    exposures: Mapping[str, Mapping[str, str | Decimal]]
    forward_returns: Mapping[str, Decimal]
    oos_forecast_before: Mapping[str, Decimal]
    oos_forecast_after: Mapping[str, Decimal]
    turnover_before: Decimal
    turnover_after: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, SignalSnapshot) or not self.baselines:
            raise InvariantViolation("NEUTRALIZATION_INPUT_INVALID")
        as_of = self._aware(self.as_of)
        cutoff = self._aware(self.information_cutoff)
        embargo = self._aware(self.embargo_until)
        available = self._aware(self.outcome_available_at)
        if cutoff > as_of or embargo <= as_of or available < embargo:
            raise InvariantViolation("NEUTRALIZATION_TEMPORAL_ORDER_INVALID")
        universe = tuple(sorted(self.universe))
        if not universe or len(set(universe)) != len(universe):
            raise InvariantViolation("NEUTRALIZATION_UNIVERSE_PIT_INVALID")
        snapshots = (self.candidate,) + self.baselines
        if len({(item.signal_id, item.signal_version) for item in snapshots}) != len(snapshots):
            raise InvariantViolation("NEUTRALIZATION_SIGNAL_IDENTITY_CONFLICT")
        for item in snapshots:
            if (item.semantic_type != "SIGNAL_VALUE" or item.as_of != utc(as_of) or
                    item.information_cutoff != utc(cutoff) or
                    item.universe_manifest_id != self.candidate.universe_manifest_id or
                    item.code_revision != self.candidate.code_revision):
                raise InvariantViolation("NEUTRALIZATION_SIGNAL_SNAPSHOT_MISMATCH")
            _signal_values(item, universe)
        maps = (self.forward_returns, self.oos_forecast_before, self.oos_forecast_after)
        if any(set(values) != set(universe) or any(not isinstance(value, Decimal) or not value.is_finite()
                                                    for value in values.values()) for values in maps):
            raise InvariantViolation("NEUTRALIZATION_VALUE_INVALID")
        exposures = {item: dict(values) for item, values in self.exposures.items()}
        if set(exposures) != set(universe):
            raise InvariantViolation("NEUTRALIZATION_EXPOSURE_INVALID")
        for values in exposures.values():
            if set(values) != _GROUPS | {"beta"} or any(not isinstance(values[name], str) or not values[name].strip()
                                                        for name in _GROUPS) or not isinstance(values["beta"], Decimal) or not values["beta"].is_finite():
                raise InvariantViolation("NEUTRALIZATION_EXPOSURE_INVALID")
        for value in (self.turnover_before, self.turnover_after):
            if not Decimal(0) <= _decimal(value, "NEUTRALIZATION_TURNOVER_INVALID") <= Decimal(1):
                raise InvariantViolation("NEUTRALIZATION_TURNOVER_INVALID")
        for name, value in (("as_of", as_of), ("information_cutoff", cutoff), ("embargo_until", embargo),
                            ("outcome_available_at", available), ("universe", universe),
                            ("exposures", MappingProxyType({key: MappingProxyType(exposures[key]) for key in universe})),
                            ("forward_returns", MappingProxyType(dict(sorted(self.forward_returns.items())))),
                            ("oos_forecast_before", MappingProxyType(dict(sorted(self.oos_forecast_before.items())))),
                            ("oos_forecast_after", MappingProxyType(dict(sorted(self.oos_forecast_after.items()))))):
            object.__setattr__(self, name, value)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise InvariantViolation("NEUTRALIZATION_TIME_NOT_AWARE")
        return value.astimezone(timezone.utc)

    def payload(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.payload(), "baselines": [item.payload() for item in self.baselines],
            "as_of": utc(self.as_of), "information_cutoff": utc(self.information_cutoff),
            "embargo_until": utc(self.embargo_until), "outcome_available_at": utc(self.outcome_available_at),
            "universe": list(self.universe),
            "exposures": {key: {name: str(value) for name, value in values.items()}
                          for key, values in self.exposures.items()},
            "forward_returns": {key: str(value) for key, value in self.forward_returns.items()},
            "oos_forecast_before": {key: str(value) for key, value in self.oos_forecast_before.items()},
            "oos_forecast_after": {key: str(value) for key, value in self.oos_forecast_after.items()},
            "turnover_before": str(self.turnover_before), "turnover_after": str(self.turnover_after),
        }


@dataclass(frozen=True, slots=True)
class NeutralizationResult:
    status: str
    reason_code: str
    report: Mapping[str, object] | None
    catalog_id: str | None


class SignalNeutralizer:
    """Evaluates redundant exposure without automatically deleting a Signal."""

    def __init__(self, store: ImmutableDatasetStore) -> None:
        self.store = store

    def evaluate(self, inputs: NeutralizationInput, *, config: NeutralizationConfig,
                 evaluated_at: datetime) -> NeutralizationResult:
        try:
            if NeutralizationInput._aware(evaluated_at) < inputs.outcome_available_at:
                raise DataQualityError("NEUTRALIZATION_OUTCOME_NOT_AVAILABLE")
            if inputs.embargo_until - inputs.as_of < config.minimum_embargo:
                raise DataQualityError("NEUTRALIZATION_EMBARGO_INSUFFICIENT")
            candidate = _signal_values(inputs.candidate, inputs.universe)
            baselines = [_signal_values(item, inputs.universe) for item in inputs.baselines]
            exposure_columns = [{item: Decimal(1) if inputs.exposures[item][group] == label else Decimal(0)
                                 for item in inputs.universe}
                                for group in _GROUPS
                                for label in sorted({inputs.exposures[item][group] for item in inputs.universe})[1:]]
            controls = baselines + [{"%s" % item: inputs.exposures[item]["beta"] for item in inputs.universe}] + exposure_columns
            residual = _residualize(candidate, controls, inputs.universe)
            outcome_residual = _residualize(dict(inputs.forward_returns), controls, inputs.universe)
            partial_ic = _pearson(list(residual.values()), list(outcome_residual.values()))
            raw_ic = _pearson(list(candidate.values()), list(inputs.forward_returns.values()))
            if raw_ic is None or partial_ic is None:
                raise DataQualityError("NEUTRALIZATION_VARIANCE_INSUFFICIENT")
            snapshots = (inputs.candidate,) + inputs.baselines
            correlations = {item.signal_id + "@" + item.signal_version: {} for item in snapshots}
            ranks = {item.signal_id + "@" + item.signal_version: _ranks(_signal_values(item, inputs.universe))
                     for item in snapshots}
            for left in snapshots:
                left_key, left_values = left.signal_id + "@" + left.signal_version, _signal_values(left, inputs.universe)
                for right in snapshots:
                    right_key, right_values = right.signal_id + "@" + right.signal_version, _signal_values(right, inputs.universe)
                    correlations[left_key][right_key] = {
                        "pearson": str(_pearson(list(left_values.values()), list(right_values.values()))),
                        "rank": str(_pearson([ranks[left_key][item] for item in inputs.universe],
                                             [ranks[right_key][item] for item in inputs.universe])),
                    }
            before_mae = _mean([abs(inputs.oos_forecast_before[item] - inputs.forward_returns[item])
                                for item in inputs.universe])
            after_mae = _mean([abs(inputs.oos_forecast_after[item] - inputs.forward_returns[item])
                               for item in inputs.universe])
            common_exposure = {
                "beta_correlation": str(_pearson(
                    [candidate[item] for item in inputs.universe],
                    [inputs.exposures[item]["beta"] for item in inputs.universe],
                )),
                "bucket_means": {
                    group: {
                        label: str(_mean([candidate[item] for item in inputs.universe
                                          if inputs.exposures[item][group] == label]))
                        for label in sorted({inputs.exposures[item][group] for item in inputs.universe})
                    }
                    for group in sorted(_GROUPS)
                },
            }
            values = {item: str(residual[item]) for item in inputs.universe}
            residual_hash = digest(canonical(values))
            report = {
                "semantic_type": "RESIDUALIZED_SIGNAL_COMPONENT",
                "candidate_signal_run_id": inputs.candidate.signal_run_id,
                "baseline_signal_run_ids": [item.signal_run_id for item in inputs.baselines],
                "universe_manifest_id": inputs.candidate.universe_manifest_id,
                "as_of": utc(inputs.as_of), "information_cutoff": utc(inputs.information_cutoff),
                "formula_version": config.formula_version, "parameter_set_id": config.parameter_set_id,
                "transform_order": ["PIT_SIGNAL_VALUES", "BASELINE_AND_COMMON_EXPOSURE_CONTROLS", "OLS_RESIDUAL"],
                "residual_values": values, "residual_output_hash": residual_hash,
                "signal_correlation_matrix": correlations, "common_exposure": common_exposure,
                "raw_ic": str(raw_ic),
                "partial_correlation": str(partial_ic), "incremental_ic": str(partial_ic),
                "oos_mae_before": str(before_mae), "oos_mae_after": str(after_mae),
                "oos_mae_improvement": str(before_mae - after_mae),
                "coverage_before": "1", "coverage_after": "1",
                "turnover_before": str(inputs.turnover_before), "turnover_after": str(inputs.turnover_after),
                "cost_before": str(config.transaction_cost_per_turnover * inputs.turnover_before),
                "cost_after": str(config.transaction_cost_per_turnover * inputs.turnover_after),
                "code_revision": inputs.candidate.code_revision,
            }
            report["neutralization_id"] = digest(canonical(report))
            return NeutralizationResult("READY", "OK", MappingProxyType(report),
                                        self.store.catalog("signal-neutralization", report))
        except (DataQualityError, InvariantViolation) as exc:
            return NeutralizationResult("ABSTAIN", str(exc), None, None)
