"""PIT, embargoed predictive diagnostics for immutable Signal snapshots."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Mapping, Sequence

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest, utc
from asset_management.domain.errors import DataQualityError, InvariantViolation
from .models import SignalSnapshot


_HASH = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"git:[0-9a-f]{7,40}")
_BUCKETS = frozenset(("sector", "size", "liquidity"))


def _aware(value: datetime, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


def _require_hash(value: str, reason: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise InvariantViolation(reason)
    return value


def _require_text(value: str, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvariantViolation(reason)
    return value


def _require_decimal(value: Decimal, reason: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantViolation(reason)
    return value


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _pearson(left: Sequence[Decimal], right: Sequence[Decimal]) -> Decimal | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean, right_mean = _mean(left), _mean(right)
    covariance = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_variance = sum((x - left_mean) ** 2 for x in left)
    right_variance = sum((y - right_mean) ** 2 for y in right)
    if left_variance == 0 or right_variance == 0:
        return None
    return covariance / (left_variance * right_variance).sqrt()


def _ranks(values: Mapping[str, Decimal]) -> dict[str, Decimal]:
    ordered, ranks, index = sorted(values.items(), key=lambda row: (row[1], row[0])), {}, 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = Decimal(index + 1 + end) / Decimal(2)
        ranks.update({instrument: rank for instrument, _ in ordered[index:end]})
        index = end
    return ranks


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True, slots=True)
class DiagnosticConfig:
    """Versioned statistical and anti-leakage requirements."""

    quantile_count: int = 5
    minimum_coverage: Decimal = Decimal("0.80")
    transaction_cost_per_turnover: Decimal = Decimal("0")
    minimum_embargo: timedelta = timedelta(days=1)
    minimum_decay_observations: int = 2
    minimum_time_series_observations: int = 3
    rolling_window: int = 3
    formula_version: str = "signal-diagnostics@1"
    parameter_set_id: str = "signal-diagnostics-defaults-v1"

    def __post_init__(self) -> None:
        valid = (
            isinstance(self.quantile_count, int) and self.quantile_count >= 2 and
            isinstance(self.minimum_decay_observations, int) and self.minimum_decay_observations >= 2 and
            isinstance(self.minimum_time_series_observations, int) and self.minimum_time_series_observations >= 3 and
            isinstance(self.rolling_window, int) and self.rolling_window >= 2 and
            isinstance(self.minimum_embargo, timedelta) and self.minimum_embargo > timedelta(0) and
            Decimal(0) < _require_decimal(self.minimum_coverage, "DIAGNOSTIC_CONFIG_INVALID") <= Decimal(1) and
            _require_decimal(self.transaction_cost_per_turnover, "DIAGNOSTIC_CONFIG_INVALID") >= 0 and
            isinstance(self.formula_version, str) and bool(self.formula_version.strip()) and
            isinstance(self.parameter_set_id, str) and bool(self.parameter_set_id.strip())
        )
        if not valid:
            raise InvariantViolation("DIAGNOSTIC_CONFIG_INVALID")

    def payload(self) -> dict[str, object]:
        return {
            "quantile_count": self.quantile_count, "minimum_coverage": str(self.minimum_coverage),
            "transaction_cost_per_turnover": str(self.transaction_cost_per_turnover),
            "minimum_embargo_seconds": int(self.minimum_embargo.total_seconds()),
            "minimum_decay_observations": self.minimum_decay_observations,
            "minimum_time_series_observations": self.minimum_time_series_observations,
            "rolling_window": self.rolling_window, "formula_version": self.formula_version,
            "parameter_set_id": self.parameter_set_id,
        }


@dataclass(frozen=True, slots=True)
class CrossSectionalObservation:
    """One historical Signal cross-section and outcomes from its exact PIT universe."""

    signal_run_id: str
    signal_id: str
    signal_version: str
    as_of: datetime
    information_cutoff: datetime
    outcome_available_at: datetime
    embargo_until: datetime
    universe_manifest_id: str
    universe: tuple[str, ...]
    signal_values: Mapping[str, Decimal | None]
    forward_returns: Mapping[str, Decimal]
    bucket_labels: Mapping[str, Mapping[str, str]]
    previous_top_quantile_members: tuple[str, ...]
    holding_period_overlap: Decimal
    horizon_days: int
    code_revision: str
    signal_snapshot: SignalSnapshot

    def __post_init__(self) -> None:
        _require_hash(self.signal_run_id, "DIAGNOSTIC_LINEAGE_INVALID")
        _require_hash(self.universe_manifest_id, "DIAGNOSTIC_LINEAGE_INVALID")
        _require_text(self.signal_id, "DIAGNOSTIC_IDENTITY_INVALID")
        _require_text(self.signal_version, "DIAGNOSTIC_IDENTITY_INVALID")
        if not isinstance(self.code_revision, str) or not _REVISION.fullmatch(self.code_revision):
            raise InvariantViolation("DIAGNOSTIC_LINEAGE_INVALID")
        as_of = _aware(self.as_of, "DIAGNOSTIC_TIME_NOT_AWARE")
        cutoff = _aware(self.information_cutoff, "DIAGNOSTIC_TIME_NOT_AWARE")
        available = _aware(self.outcome_available_at, "DIAGNOSTIC_TIME_NOT_AWARE")
        embargo = _aware(self.embargo_until, "DIAGNOSTIC_TIME_NOT_AWARE")
        if cutoff > as_of or embargo <= as_of or available < embargo:
            raise InvariantViolation("DIAGNOSTIC_TEMPORAL_ORDER_INVALID")
        if (not isinstance(self.horizon_days, int) or self.horizon_days <= 0 or
                not Decimal(0) <= _require_decimal(self.holding_period_overlap,
                                                    "DIAGNOSTIC_OBSERVATION_INVALID") <= Decimal(1)):
            raise InvariantViolation("DIAGNOSTIC_OBSERVATION_INVALID")
        universe = tuple(sorted(self.universe))
        if not universe or len(set(universe)) != len(universe) or any(
                not isinstance(item, str) or not item.strip() for item in universe):
            raise InvariantViolation("DIAGNOSTIC_UNIVERSE_INVALID")
        values, returns = dict(sorted(self.signal_values.items())), dict(sorted(self.forward_returns.items()))
        if set(values) != set(universe) or set(returns) != set(universe):
            raise InvariantViolation("DIAGNOSTIC_UNIVERSE_PIT_INVALID")
        if any(value is not None and (not isinstance(value, Decimal) or not value.is_finite())
               for value in values.values()) or any(not isinstance(value, Decimal) or not value.is_finite()
                                                    for value in returns.values()):
            raise InvariantViolation("DIAGNOSTIC_VALUE_INVALID")
        snapshot = self.signal_snapshot
        expected_values = {key: None if value is None else str(value) for key, value in values.items()}
        if (not isinstance(snapshot, SignalSnapshot) or snapshot.semantic_type != "SIGNAL_VALUE" or
                snapshot.signal_run_id != self.signal_run_id or snapshot.signal_id != self.signal_id or
                snapshot.signal_version != self.signal_version or snapshot.universe_manifest_id != self.universe_manifest_id or
                snapshot.code_revision != self.code_revision or snapshot.as_of != utc(as_of) or
                snapshot.information_cutoff != utc(cutoff) or dict(snapshot.values) != expected_values):
            raise InvariantViolation("DIAGNOSTIC_SIGNAL_SNAPSHOT_MISMATCH")
        labels = {instrument: dict(sorted(group.items())) for instrument, group in self.bucket_labels.items()}
        if set(labels) != set(universe) or any(
                set(group) != _BUCKETS or any(not isinstance(value, str) or not value.strip()
                                               for value in group.values()) for group in labels.values()):
            raise InvariantViolation("DIAGNOSTIC_BUCKETS_INVALID")
        previous = tuple(sorted(self.previous_top_quantile_members))
        if len(set(previous)) != len(previous) or any(not isinstance(item, str) or not item.strip()
                                                       for item in previous):
            raise InvariantViolation("DIAGNOSTIC_TURNOVER_INVALID")
        for field, value in (
            ("as_of", as_of), ("information_cutoff", cutoff), ("outcome_available_at", available),
            ("embargo_until", embargo), ("universe", universe), ("previous_top_quantile_members", previous),
            ("signal_values", MappingProxyType(values)), ("forward_returns", MappingProxyType(returns)),
            ("bucket_labels", MappingProxyType({key: MappingProxyType(labels[key]) for key in universe})),
        ):
            object.__setattr__(self, field, value)

    def payload(self) -> dict[str, object]:
        return {
            "signal_run_id": self.signal_run_id, "signal_id": self.signal_id,
            "signal_version": self.signal_version, "as_of": utc(self.as_of),
            "information_cutoff": utc(self.information_cutoff),
            "outcome_available_at": utc(self.outcome_available_at), "embargo_until": utc(self.embargo_until),
            "universe_manifest_id": self.universe_manifest_id, "universe": list(self.universe),
            "signal_values": {key: _text(value) for key, value in self.signal_values.items()},
            "forward_returns": {key: str(value) for key, value in self.forward_returns.items()},
            "bucket_labels": {key: dict(value) for key, value in self.bucket_labels.items()},
            "previous_top_quantile_members": list(self.previous_top_quantile_members),
            "holding_period_overlap": str(self.holding_period_overlap),
            "horizon_days": self.horizon_days, "code_revision": self.code_revision,
            "signal_snapshot": self.signal_snapshot.payload(),
        }


@dataclass(frozen=True, slots=True)
class TimeSeriesObservation:
    """One embargoed, completed out-of-sample ETF forecast."""

    signal_run_id: str
    signal_id: str
    signal_version: str
    as_of: datetime
    information_cutoff: datetime
    outcome_available_at: datetime
    embargo_until: datetime
    universe_manifest_id: str
    forecast: Decimal
    realized_return: Decimal
    implementation_cost: Decimal
    instrument_id: str
    regime: str
    drawdown: Decimal
    horizon_days: int
    code_revision: str
    signal_snapshot: SignalSnapshot

    def __post_init__(self) -> None:
        _require_hash(self.signal_run_id, "DIAGNOSTIC_LINEAGE_INVALID")
        _require_hash(self.universe_manifest_id, "DIAGNOSTIC_LINEAGE_INVALID")
        for value in (self.signal_id, self.signal_version, self.regime):
            _require_text(value, "DIAGNOSTIC_IDENTITY_INVALID")
        _require_text(self.instrument_id, "DIAGNOSTIC_IDENTITY_INVALID")
        if not isinstance(self.code_revision, str) or not _REVISION.fullmatch(self.code_revision):
            raise InvariantViolation("DIAGNOSTIC_LINEAGE_INVALID")
        as_of = _aware(self.as_of, "DIAGNOSTIC_TIME_NOT_AWARE")
        cutoff = _aware(self.information_cutoff, "DIAGNOSTIC_TIME_NOT_AWARE")
        available = _aware(self.outcome_available_at, "DIAGNOSTIC_TIME_NOT_AWARE")
        embargo = _aware(self.embargo_until, "DIAGNOSTIC_TIME_NOT_AWARE")
        if cutoff > as_of or embargo <= as_of or available < embargo:
            raise InvariantViolation("DIAGNOSTIC_TEMPORAL_ORDER_INVALID")
        if (not isinstance(self.horizon_days, int) or self.horizon_days <= 0 or
                _require_decimal(self.implementation_cost, "DIAGNOSTIC_VALUE_INVALID") < 0 or
                _require_decimal(self.drawdown, "DIAGNOSTIC_VALUE_INVALID") > 0):
            raise InvariantViolation("DIAGNOSTIC_OBSERVATION_INVALID")
        _require_decimal(self.forecast, "DIAGNOSTIC_VALUE_INVALID")
        _require_decimal(self.realized_return, "DIAGNOSTIC_VALUE_INVALID")
        snapshot = self.signal_snapshot
        if (not isinstance(snapshot, SignalSnapshot) or snapshot.semantic_type != "SIGNAL_VALUE" or
                snapshot.signal_run_id != self.signal_run_id or snapshot.signal_id != self.signal_id or
                snapshot.signal_version != self.signal_version or snapshot.universe_manifest_id != self.universe_manifest_id or
                snapshot.code_revision != self.code_revision or snapshot.as_of != utc(as_of) or
                snapshot.information_cutoff != utc(cutoff) or
                dict(snapshot.values) != {self.instrument_id: str(self.forecast)}):
            raise InvariantViolation("DIAGNOSTIC_SIGNAL_SNAPSHOT_MISMATCH")
        for field, value in (("as_of", as_of), ("information_cutoff", cutoff),
                             ("outcome_available_at", available), ("embargo_until", embargo)):
            object.__setattr__(self, field, value)

    def payload(self) -> dict[str, object]:
        return {
            "signal_run_id": self.signal_run_id, "signal_id": self.signal_id,
            "signal_version": self.signal_version, "as_of": utc(self.as_of),
            "information_cutoff": utc(self.information_cutoff),
            "outcome_available_at": utc(self.outcome_available_at), "embargo_until": utc(self.embargo_until),
            "universe_manifest_id": self.universe_manifest_id, "forecast": str(self.forecast),
            "realized_return": str(self.realized_return), "implementation_cost": str(self.implementation_cost),
            "instrument_id": self.instrument_id, "regime": self.regime, "drawdown": str(self.drawdown),
            "horizon_days": self.horizon_days, "code_revision": self.code_revision,
            "signal_snapshot": self.signal_snapshot.payload(),
        }


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Complete immutable record of metrics, input lineage, and calculation identity."""

    diagnostics_id: str
    diagnostic_type: str
    signal_id: str
    signal_version: str
    signal_run_ids: tuple[str, ...]
    universe_manifest_ids: tuple[str, ...]
    evaluated_at: str
    formula_version: str
    parameter_set_id: str
    code_revision: str
    input_hash: str
    metrics: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.diagnostic_type not in {"CROSS_SECTIONAL", "IC_DECAY", "TIME_SERIES"} or not self.metrics:
            raise InvariantViolation("DIAGNOSTIC_REPORT_INVALID")
        for value in (self.diagnostics_id, self.input_hash):
            _require_hash(value, "DIAGNOSTIC_REPORT_INVALID")
        for value in (self.signal_id, self.signal_version, self.evaluated_at,
                      self.formula_version, self.parameter_set_id):
            _require_text(value, "DIAGNOSTIC_REPORT_INVALID")
        if not _REVISION.fullmatch(self.code_revision):
            raise InvariantViolation("DIAGNOSTIC_REPORT_INVALID")
        run_ids, universe_ids = tuple(sorted(self.signal_run_ids)), tuple(sorted(self.universe_manifest_ids))
        if (not run_ids or not universe_ids or len(set(run_ids)) != len(run_ids) or
                len(set(universe_ids)) != len(universe_ids) or
                any(not _HASH.fullmatch(value) for value in run_ids + universe_ids)):
            raise InvariantViolation("DIAGNOSTIC_REPORT_INVALID")
        object.__setattr__(self, "signal_run_ids", run_ids)
        object.__setattr__(self, "universe_manifest_ids", universe_ids)
        object.__setattr__(self, "metrics", MappingProxyType(dict(sorted(self.metrics.items()))))

    def payload(self) -> dict[str, object]:
        return {
            "diagnostics_id": self.diagnostics_id, "diagnostic_type": self.diagnostic_type,
            "signal_id": self.signal_id, "signal_version": self.signal_version,
            "signal_run_ids": list(self.signal_run_ids), "universe_manifest_ids": list(self.universe_manifest_ids),
            "evaluated_at": self.evaluated_at, "formula_version": self.formula_version,
            "parameter_set_id": self.parameter_set_id, "code_revision": self.code_revision,
            "input_hash": self.input_hash, "metrics": dict(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class DiagnosticRunResult:
    status: str
    reason_code: str
    report: DiagnosticReport | None
    catalog_id: str | None


class SignalDiagnosticsStore:
    """Read-only calculation and immutable catalog publication for Signal diagnostics."""

    def __init__(self, store: ImmutableDatasetStore) -> None:
        self.store = store

    def evaluate_cross_sectional(self, observation: CrossSectionalObservation, *, config: DiagnosticConfig,
                                 evaluated_at: datetime) -> DiagnosticRunResult:
        try:
            evaluated = self._eligible_cross(observation, config, evaluated_at)
            return self._publish(
                "CROSS_SECTIONAL", observation.signal_id, observation.signal_version,
                (observation.signal_run_id,), (observation.universe_manifest_id,), evaluated, config,
                observation.code_revision, [observation.payload()], self._cross_metrics(observation, config),
            )
        except (DataQualityError, InvariantViolation) as exc:
            return self._abstain(str(exc))

    def evaluate_ic_decay(self, observations: Sequence[CrossSectionalObservation], *, config: DiagnosticConfig,
                          evaluated_at: datetime) -> DiagnosticRunResult:
        try:
            if not observations:
                raise DataQualityError("DIAGNOSTIC_OBSERVATIONS_MISSING")
            first = observations[0]
            grouped: dict[int, list[dict[str, object]]] = defaultdict(list)
            for item in observations:
                self._eligible_cross(item, config, evaluated_at)
                if (item.signal_id, item.signal_version, item.code_revision) != (
                        first.signal_id, first.signal_version, first.code_revision):
                    raise DataQualityError("DIAGNOSTIC_IDENTITY_CONFLICT")
                grouped[item.horizon_days].append(self._cross_metrics(item, config))
            decay: dict[str, object] = {}
            for horizon, metrics in sorted(grouped.items()):
                if len(metrics) < config.minimum_decay_observations:
                    raise DataQualityError("DIAGNOSTIC_DECAY_HISTORY_INSUFFICIENT")
                decay[str(horizon)] = {
                    "observation_count": len(metrics),
                    "pearson_ic": str(_mean([Decimal(str(metric["pearson_ic"])) for metric in metrics])),
                    "spearman_rank_ic": str(_mean([Decimal(str(metric["spearman_rank_ic"])) for metric in metrics])),
                    "post_cost_spread": str(_mean([Decimal(str(metric["post_cost_spread"])) for metric in metrics])),
                }
            return self._publish(
                "IC_DECAY", first.signal_id, first.signal_version,
                tuple(item.signal_run_id for item in observations),
                tuple(item.universe_manifest_id for item in observations),
                _aware(evaluated_at, "DIAGNOSTIC_TIME_NOT_AWARE"), config, first.code_revision,
                [item.payload() for item in observations],
                {"horizon_ic_decay": decay, "horizon_count": len(decay)},
            )
        except (DataQualityError, InvariantViolation) as exc:
            return self._abstain(str(exc))

    def evaluate_time_series(self, observations: Sequence[TimeSeriesObservation], *, config: DiagnosticConfig,
                             evaluated_at: datetime) -> DiagnosticRunResult:
        try:
            if len(observations) < config.minimum_time_series_observations:
                raise DataQualityError("DIAGNOSTIC_TIME_SERIES_HISTORY_INSUFFICIENT")
            evaluated, first = _aware(evaluated_at, "DIAGNOSTIC_TIME_NOT_AWARE"), observations[0]
            ordered = sorted(observations, key=lambda item: item.as_of)
            if len({item.as_of for item in ordered}) != len(ordered):
                raise DataQualityError("DIAGNOSTIC_TIME_SERIES_DUPLICATE_AS_OF")
            for item in ordered:
                self._eligible_time(item, config, evaluated)
                if (item.signal_id, item.signal_version, item.instrument_id, item.code_revision) != (
                        first.signal_id, first.signal_version, first.instrument_id, first.code_revision):
                    raise DataQualityError("DIAGNOSTIC_IDENTITY_CONFLICT")
            forecasts, realized = [item.forecast for item in ordered], [item.realized_return for item in ordered]
            errors, correlation = [forecast - actual for forecast, actual in zip(forecasts, realized)], _pearson(forecasts, realized)
            if correlation is None:
                raise DataQualityError("DIAGNOSTIC_VARIANCE_INSUFFICIENT")
            rolling = {
                utc(ordered[index - 1].as_of): _text(_pearson(
                    forecasts[index - config.rolling_window:index],
                    realized[index - config.rolling_window:index],
                ))
                for index in range(config.rolling_window, len(ordered) + 1)
            }
            regimes: dict[str, list[TimeSeriesObservation]] = defaultdict(list)
            for item in ordered:
                regimes[item.regime].append(item)
            regime_metrics = {
                regime: {
                    "observation_count": len(items),
                    "mean_absolute_error": str(_mean([abs(item.forecast - item.realized_return) for item in items])),
                    "post_cost_utility": str(_mean([item.realized_return - item.implementation_cost for item in items])),
                    "correlation": _text(_pearson([item.forecast for item in items],
                                                   [item.realized_return for item in items])),
                }
                for regime, items in sorted(regimes.items())
            }
            metrics = {
                "observation_count": len(ordered), "calibration_bias": str(_mean(errors)),
                "mean_absolute_error": str(_mean([abs(error) for error in errors])),
                "root_mean_squared_error": str(_mean([error ** 2 for error in errors]).sqrt()),
                "sign_accuracy": str(Decimal(sum(forecast * actual > 0 for forecast, actual in zip(forecasts, realized))) /
                                     Decimal(len(ordered))),
                "forecast_realized_correlation": str(correlation),
                "post_cost_utility": str(_mean([item.realized_return - item.implementation_cost for item in ordered])),
                "rolling_correlation": rolling, "regime_stability": regime_metrics,
                "drawdown_error_correlation": _text(_pearson([abs(error) for error in errors],
                                                              [item.drawdown for item in ordered])),
            }
            return self._publish(
                "TIME_SERIES", first.signal_id, first.signal_version,
                tuple(item.signal_run_id for item in ordered),
                tuple(item.universe_manifest_id for item in ordered), evaluated, config, first.code_revision,
                [item.payload() for item in ordered], metrics,
            )
        except (DataQualityError, InvariantViolation) as exc:
            return self._abstain(str(exc))

    @staticmethod
    def _eligible_cross(observation: CrossSectionalObservation, config: DiagnosticConfig,
                        evaluated_at: datetime) -> datetime:
        evaluated = _aware(evaluated_at, "DIAGNOSTIC_TIME_NOT_AWARE")
        if observation.embargo_until - observation.as_of < config.minimum_embargo:
            raise DataQualityError("DIAGNOSTIC_EMBARGO_INSUFFICIENT")
        if evaluated < observation.outcome_available_at:
            raise DataQualityError("DIAGNOSTIC_OUTCOME_NOT_AVAILABLE")
        count = sum(value is not None for value in observation.signal_values.values())
        if Decimal(count) / Decimal(len(observation.universe)) < config.minimum_coverage:
            raise DataQualityError("DIAGNOSTIC_COVERAGE_INSUFFICIENT")
        if count < config.quantile_count:
            raise DataQualityError("DIAGNOSTIC_QUANTILES_INSUFFICIENT")
        return evaluated

    @staticmethod
    def _eligible_time(observation: TimeSeriesObservation, config: DiagnosticConfig,
                       evaluated_at: datetime) -> None:
        if observation.embargo_until - observation.as_of < config.minimum_embargo:
            raise DataQualityError("DIAGNOSTIC_EMBARGO_INSUFFICIENT")
        if evaluated_at < observation.outcome_available_at:
            raise DataQualityError("DIAGNOSTIC_OUTCOME_NOT_AVAILABLE")

    @staticmethod
    def _cross_metrics(observation: CrossSectionalObservation, config: DiagnosticConfig) -> dict[str, object]:
        values = {key: value for key, value in observation.signal_values.items() if value is not None}
        returns = {key: observation.forward_returns[key] for key in values}
        pearson = _pearson(list(values.values()), list(returns.values()))
        ranks = _ranks(values)
        rank_ic = _pearson([ranks[key] for key in values], [returns[key] for key in values])
        if pearson is None or rank_ic is None:
            raise DataQualityError("DIAGNOSTIC_VARIANCE_INSUFFICIENT")
        ordered, groups = sorted(values, key=lambda key: (values[key], key)), defaultdict(list)
        for index, instrument in enumerate(ordered):
            groups[index * config.quantile_count // len(ordered) + 1].append(instrument)
        quantiles = {f"Q{number}": str(_mean([returns[item] for item in items]))
                     for number, items in sorted(groups.items())}
        gross = Decimal(quantiles[f"Q{config.quantile_count}"]) - Decimal(quantiles["Q1"])
        top = set(groups[config.quantile_count])
        turnover = (Decimal(1) if not observation.previous_top_quantile_members else
                    Decimal(1) - Decimal(len(top & set(observation.previous_top_quantile_members))) / Decimal(len(top)))
        buckets: dict[str, object] = {}
        for category in sorted(_BUCKETS):
            buckets[category] = {}
            for label in sorted({observation.bucket_labels[item][category] for item in observation.universe}):
                members = [item for item in observation.universe if observation.bucket_labels[item][category] == label]
                available = [item for item in members if item in values]
                buckets[category][label] = {
                    "member_count": len(members), "coverage": str(Decimal(len(available)) / Decimal(len(members))),
                    "pearson_ic": _text(_pearson([values[item] for item in available],
                                                  [returns[item] for item in available])),
                    "hit_rate": (str(Decimal(sum(values[item] * returns[item] > 0 for item in available)) /
                                     Decimal(len(available))) if available else None),
                }
        return {
            "coverage": str(Decimal(len(values)) / Decimal(len(observation.universe))),
            "pearson_ic": str(pearson), "spearman_rank_ic": str(rank_ic), "quantile_returns": quantiles,
            "gross_spread": str(gross),
            "post_cost_spread": str(gross - Decimal(2) * config.transaction_cost_per_turnover * turnover),
            "hit_rate": str(Decimal(sum(values[item] * returns[item] > 0 for item in values)) / Decimal(len(values))),
            "turnover": str(turnover), "holding_period_overlap": str(observation.holding_period_overlap),
            "bucket_stability": buckets,
        }

    def _publish(self, kind: str, signal_id: str, signal_version: str, run_ids: tuple[str, ...],
                 universe_ids: tuple[str, ...], evaluated_at: datetime, config: DiagnosticConfig,
                 code_revision: str, observations: list[dict[str, object]], metrics: dict[str, object]) -> DiagnosticRunResult:
        inputs = {"diagnostic_type": kind, "observations": observations, "config": config.payload(),
                  "evaluated_at": utc(evaluated_at)}
        input_hash = digest(canonical(inputs))
        identity = {"input_hash": input_hash, "metrics": metrics, "signal_id": signal_id,
                    "signal_version": signal_version, "code_revision": code_revision}
        report = DiagnosticReport(
            digest(canonical(identity)), kind, signal_id, signal_version, run_ids,
            tuple(sorted(set(universe_ids))),
            utc(evaluated_at), config.formula_version, config.parameter_set_id, code_revision, input_hash, metrics,
        )
        return DiagnosticRunResult("READY", "OK", report, self.store.catalog("signal-diagnostics", report.payload()))

    @staticmethod
    def _abstain(reason_code: str) -> DiagnosticRunResult:
        return DiagnosticRunResult("ABSTAIN", reason_code, None, None)
