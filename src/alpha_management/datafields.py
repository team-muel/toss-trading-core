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
