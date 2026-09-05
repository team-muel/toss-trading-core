"""Provider-independent datafield access for alpha research.

The alpha layer deliberately does not know how Toss, FRED, SEC, or any other
provider is reached.  It consumes values already admitted by the
`asset_management` time/data/reference contracts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from asset_management.data.asof_query import AsOfRepository
from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.data.phase9 import LatestSuccessfulDataset
from asset_management.domain.errors import DataQualityError
from asset_management.reference.universe import UniverseRepository
from asset_management.time.asof import AsOfContext, require_as_of_context

NumericInput = int | float | str


class AssetDataSource(Protocol):
    """Read-only bridge from asset-management data truth into alpha research."""

    def cross_section(
        self,
        field: str,
        *,
        universe: str,
        context: AsOfContext,
    ) -> Mapping[str, NumericInput]: ...

    def time_series(
        self,
        field: str,
        *,
        instrument_id: str,
        context: AsOfContext,
    ) -> Sequence[NumericInput]: ...


@dataclass(frozen=True, slots=True)
class PointInTimeDataSource:
    """Concrete, read-only bridge over data, manifest, and reference truth."""

    observations: AsOfRepository
    datasets: ImmutableDatasetStore
    universes: UniverseRepository
    source: str
    dataset: str

    def _manifest_id(self, context: AsOfContext) -> str:
        context = require_as_of_context(context)
        return LatestSuccessfulDataset(self.datasets).get(
            source=self.source,
            dataset=self.dataset,
            cutoff=context.information_cutoff_utc,
        ).manifest_id

    def cross_section(
        self, field: str, *, universe: str, context: AsOfContext,
    ) -> Mapping[str, NumericInput]:
        manifest_id = self._manifest_id(context)
        members = self.universes.members(universe, context)
        if not members:
            raise DataQualityError("ALPHA_UNIVERSE_EMPTY")
        values: dict[str, NumericInput] = {}
        for instrument_id in members:
            observation = self.observations.get_latest(
                entity_id=instrument_id,
                field=field,
                context=context,
                dataset_manifest_id=manifest_id,
            )
            values[instrument_id] = observation.value
        return values

    def time_series(
        self, field: str, *, instrument_id: str, context: AsOfContext,
    ) -> Sequence[NumericInput]:
        manifest_id = self._manifest_id(context)
        # Membership is not required for a historical single-name read, but the
        # instrument must exist and be active at the requested as-of instant.
        self.universes.get(instrument_id, context)
        observations = self.observations.series(
            entity_id=instrument_id,
            field=field,
            context=context,
            dataset_manifest_id=manifest_id,
        )
        return [observation.value for observation in observations]


def _number(value: NumericInput) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError("alpha datafield values must be finite")
    return number


@dataclass(frozen=True, slots=True)
class RepositoryDataFields:
    """Alpha-facing view over a validated repository data source.

    `AsOfContext` is mandatory.  This preserves the Phase 6 point-in-time
    contract: alpha expressions cannot silently read information that became
    available after the research cutoff.
    """

    source: AssetDataSource

    def cross_section(
        self,
        field: str,
        *,
        universe: str,
        context: AsOfContext,
    ) -> dict[str, float]:
        require_as_of_context(context)
        if not field.strip() or not universe.strip():
            raise ValueError("field and universe cannot be blank")
        values = self.source.cross_section(field, universe=universe, context=context)
        return {instrument_id: _number(value) for instrument_id, value in values.items()}

    def time_series(
        self,
        field: str,
        *,
        instrument_id: str,
        context: AsOfContext,
    ) -> list[float]:
        require_as_of_context(context)
        if not field.strip() or not instrument_id.strip():
            raise ValueError("field and instrument_id cannot be blank")
        values = self.source.time_series(field, instrument_id=instrument_id, context=context)
        return [_number(value) for value in values]
