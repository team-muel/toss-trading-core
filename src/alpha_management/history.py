"""Point-in-time historical simulation for compiled alpha expressions.

Delay selects a prior trading session before expression evaluation.  Decay is
then applied to simulated position weights, never to raw data or alpha values.
The module is research-only and deliberately has no order/execution boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from asset_management.time.asof import AsOfContext, require_as_of_context

from .dsl import CompiledExpression, Panel, PanelResolver, RepositoryPanelResolver
from .expression import Alpha, AlphaSimulationSettings, simulate_cross_section
from .metrics import SimulationResult, evaluate


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class HistoricalSession:
    """One ordered trading session and the information knowable at its cutoff."""

    effective_time_utc: datetime
    context: AsOfContext
    resolver: PanelResolver
    instrument_ids: tuple[str, ...]
    dataset_manifest_ids: tuple[str, ...] = ()
    universe_version: str = "unspecified"
    neutralization_groups: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_utc(self.effective_time_utc, "effective_time_utc")
        require_as_of_context(self.context)
        if (
            isinstance(self.resolver, RepositoryPanelResolver)
            and self.resolver.context != self.context
        ):
            raise ValueError("repository resolver context must match session context")
        if isinstance(self.resolver, RepositoryPanelResolver):
            derived = self.resolver.dataset_manifest_ids
            if derived and self.dataset_manifest_ids and derived != self.dataset_manifest_ids:
                raise ValueError("session manifest IDs do not match repository resolver")
            if derived:
                object.__setattr__(self, "dataset_manifest_ids", derived)
        if self.context.as_of_utc != self.effective_time_utc:
            raise ValueError("session effective time must equal context.as_of_utc")
        if not self.instrument_ids or len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("instrument_ids must be non-empty and unique")
        if not self.universe_version.strip():
            raise ValueError("universe_version cannot be blank")


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    """Weights effective at one session, with their exact source lineage."""

    effective_time_utc: datetime
    signal_time_utc: datetime | None
    information_cutoff_utc: datetime | None
    raw: Mapping[str, float | None]
    base_weights: Mapping[str, float | None]
    weights: Mapping[str, float | None]
    dataset_manifest_ids: tuple[str, ...]
    universe_version: str
    signal_universe_version: str | None
    source_run_id: str | None
    code_revision: str | None
    neutralization_groups: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class HistorySimulationResult:
    """Auditable historical panel produced without live trading side effects."""

    expression: str
    expression_hash: str
    settings: AlphaSimulationSettings
    points: tuple[HistoryPoint, ...]
    metrics: SimulationResult | None = None

    @property
    def position_panel(self) -> dict[str, list[float | None]]:
        instruments = dict.fromkeys(
            instrument_id
            for point in self.points
            for instrument_id in point.weights
        )
        return {
            instrument_id: [point.weights.get(instrument_id) for point in self.points]
            for instrument_id in instruments
        }


def _last_cross_section(
    expression: CompiledExpression,
    session: HistoricalSession,
    target_instruments: Sequence[str],
) -> dict[str, float | None]:
    panel = expression.evaluate(session.resolver)
    result: dict[str, float | None] = {}
    for instrument_id in target_instruments:
        series = panel.get(instrument_id)
        result[instrument_id] = None if not series else series[-1]
    return result


def _decayed_weights(
    base_history: Sequence[Mapping[str, float | None]],
    instruments: Sequence[str],
    decay: int,
) -> dict[str, float | None]:
    if decay <= 1:
        current = base_history[-1]
        return {instrument_id: current.get(instrument_id) for instrument_id in instruments}
    if len(base_history) < decay:
        return {instrument_id: None for instrument_id in instruments}
    window = base_history[-decay:]
    denominator = decay * (decay + 1) / 2
    result: dict[str, float | None] = {}
    for instrument_id in instruments:
        values = [weights.get(instrument_id) for weights in window]
        result[instrument_id] = (
            None
            if any(value is None for value in values)
            else sum((index + 1) * float(value) for index, value in enumerate(values))
            / denominator
        )
    return result


def simulate_history(
    expression: CompiledExpression,
    sessions: Sequence[HistoricalSession],
    settings: AlphaSimulationSettings,
    *,
    forward_returns: Panel | None = None,
) -> HistorySimulationResult:
    """Evaluate delay and decay over explicitly ordered trading sessions.

    The first ``delay`` sessions are unavailable.  A position decay window is
    also unavailable until every base position in that window exists; neither
    warm-up is silently zero-filled.
    """

    if not sessions:
        raise ValueError("sessions cannot be empty")
    effective_times = [session.effective_time_utc for session in sessions]
    if effective_times != sorted(effective_times) or len(set(effective_times)) != len(effective_times):
        raise ValueError("sessions must be strictly increasing by effective time")

    points: list[HistoryPoint] = []
    base_history: list[dict[str, float | None]] = []
    for index, effective_session in enumerate(sessions):
        instruments = effective_session.instrument_ids
        source_index = index - settings.delay
        if source_index < 0:
            raw = {instrument_id: None for instrument_id in instruments}
            base = dict(raw)
            source = None
        else:
            source = sessions[source_index]
            raw = _last_cross_section(expression, source, instruments)
            available_raw = {
                instrument_id: float(value)
                for instrument_id, value in raw.items()
                if value is not None
            }
            if available_raw:
                alpha = Alpha(expression.canonical, lambda _context, values=available_raw: values)
                transformed = simulate_cross_section(
                    alpha,
                    {},
                    settings,
                    groups=source.neutralization_groups,
                ).weights
            else:
                transformed = {}
            base = {
                instrument_id: transformed.get(instrument_id)
                for instrument_id in instruments
            }
        base_history.append(base)
        weights = _decayed_weights(base_history, instruments, settings.decay)
        points.append(HistoryPoint(
            effective_time_utc=effective_session.effective_time_utc,
            signal_time_utc=None if source is None else source.effective_time_utc,
            information_cutoff_utc=None if source is None else source.context.information_cutoff_utc,
            raw=raw,
            base_weights=base,
            weights=weights,
            dataset_manifest_ids=() if source is None else source.dataset_manifest_ids,
            universe_version=effective_session.universe_version,
            signal_universe_version=None if source is None else source.universe_version,
            source_run_id=None if source is None else source.context.run_id,
            code_revision=None if source is None else source.context.code_revision,
            neutralization_groups={} if source is None else dict(source.neutralization_groups),
        ))
    result = HistorySimulationResult(
        expression=expression.canonical,
        expression_hash=expression.expression_hash,
        settings=settings,
        points=tuple(points),
    )
    if forward_returns is None:
        return result
    panel = result.position_panel
    expected_length = len(result.points)
    if any(len(series) != expected_length for series in forward_returns.values()):
        raise ValueError("forward_returns must align with the simulation timeline")
    held_instruments = {
        instrument_id
        for instrument_id, series in panel.items()
        if any(value is not None for value in series)
    }
    missing_returns = held_instruments - set(forward_returns)
    if missing_returns:
        raise ValueError(
            f"forward_returns misses held instruments: {sorted(missing_returns)}"
        )
    available = [
        index
        for index in range(expected_length)
        if any(series[index] is not None for series in panel.values())
    ]
    scored_positions = {
        instrument_id: [series[index] for index in available]
        for instrument_id, series in panel.items()
    }
    scored_returns = {
        instrument_id: [series[index] for index in available]
        for instrument_id, series in forward_returns.items()
    }
    return HistorySimulationResult(
        expression=result.expression,
        expression_hash=result.expression_hash,
        settings=result.settings,
        points=result.points,
        metrics=evaluate(scored_positions, scored_returns, settings.book_size),
    )
