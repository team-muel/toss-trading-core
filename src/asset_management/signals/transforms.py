"""Governed canonical Signal transform implementations.

Signal definitions name one of these repository-owned callables.  Callers may
request a transform but cannot replace the implementation by spoofing its name.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Mapping


def rank_transform(values: Mapping[str, Mapping[str, Decimal]]) -> Mapping[str, Decimal]:
    """Deterministic ordinal cross-sectional rank over a single source feature."""
    if not values:
        raise ValueError("SIGNAL_TRANSFORM_INPUT_EMPTY")
    feature_ids = {tuple(sorted(row)) for row in values.values()}
    if len(feature_ids) != 1 or len(next(iter(feature_ids))) != 1:
        raise ValueError("SIGNAL_TRANSFORM_SOURCE_SHAPE_INVALID")
    feature_id = next(iter(feature_ids))[0]
    ordered = sorted(values, key=lambda item: (values[item][feature_id], item))
    return {
        instrument: Decimal(index + 1) / Decimal(len(ordered))
        for index, instrument in enumerate(ordered)
    }
