"""Alpha operator library (research-only).

This module is a small, dependency-free port of the operator vocabulary used to
author *fast-expression* alphas on research platforms such as WorldQuant BRAIN.
It intentionally uses only the Python standard library so it can slot into this
repository without adding a numpy/pandas dependency.

Two shapes are used throughout:

- A *cross-section* is a ``Mapping[str, float]`` from symbol to value at one
  point in time.  Cross-sectional operators (``rank``, ``zscore``, ``scale``,
  ``group_neutralize`` ...) return a new ``dict[str, float]``.
- A *time series* is a ``Sequence[float]`` ordered oldest to newest for a single
  symbol.  Time-series operators (``ts_mean``, ``ts_delta``, ``ts_rank`` ...)
  return a ``list[float | None]`` of the same length, with ``None`` during the
  warm-up window so callers never confuse "not enough history" with a real 0.

Nothing here places orders.  Operators only transform decision inputs; the
existing ``RiskHub`` and live gates remain the sole authority over execution.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import sqrt

Number = float

__all__ = [
    "rank",
    "zscore",
    "scale",
    "sign",
    "winsorize",
    "group_neutralize",
    "group_rank",
    "truncate",
    "ts_delay",
    "ts_delta",
    "ts_sum",
    "ts_mean",
    "ts_stddev",
    "ts_zscore",
    "ts_rank",
    "ts_decay_linear",
    "ts_max",
    "ts_min",
]


def _clean(values: Mapping[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in values.items():
        if value is None:
            continue
        number = float(value)
        if number != number:  # NaN guard
            continue
        out[key] = number
    return out


def _average_ranks(values: dict[str, float]) -> dict[str, float]:
    """Return 1-based average ranks, ties sharing the mean of their positions."""
    ordered = sorted(values.items(), key=lambda item: item[1])
    ranks: dict[str, float] = {}
    index = 0
    n = len(ordered)
    while index < n:
        stop = index
        while stop + 1 < n and ordered[stop + 1][1] == ordered[index][1]:
            stop += 1
        average = (index + stop) / 2 + 1  # positions are 0-based, ranks 1-based
        for pos in range(index, stop + 1):
            ranks[ordered[pos][0]] = average
        index = stop + 1
    return ranks


def rank(values: Mapping[str, float]) -> dict[str, float]:
    """Cross-sectional rank normalised to ``[0, 1]`` (ties get the average)."""
    clean = _clean(values)
    if not clean:
        return {}
    if len(clean) == 1:
        return {next(iter(clean)): 0.5}
    ranks = _average_ranks(clean)
    n = len(clean)
    return {key: (value - 1) / (n - 1) for key, value in ranks.items()}


def zscore(values: Mapping[str, float]) -> dict[str, float]:
    """Cross-sectional z-score using the population standard deviation."""
    clean = _clean(values)
    if not clean:
        return {}
    mean = sum(clean.values()) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean.values()) / len(clean)
    std = sqrt(variance)
    if std == 0:
        return {key: 0.0 for key in clean}
    return {key: (value - mean) / std for key, value in clean.items()}


def scale(values: Mapping[str, float], target: float = 1.0) -> dict[str, float]:
    """Scale so the sum of absolute values equals ``target`` (BRAIN ``scale``)."""
    clean = _clean(values)
    gross = sum(abs(value) for value in clean.values())
    if gross == 0:
        return {key: 0.0 for key in clean}
    factor = target / gross
    return {key: value * factor for key, value in clean.items()}


def sign(values: Mapping[str, float]) -> dict[str, float]:
    clean = _clean(values)
    return {key: (1.0 if value > 0 else -1.0 if value < 0 else 0.0) for key, value in clean.items()}


def winsorize(values: Mapping[str, float], std: float = 4.0) -> dict[str, float]:
    """Clip outliers to ``mean +/- std * sigma`` before ranking/scaling."""
    clean = _clean(values)
    if not clean:
        return {}
    mean = sum(clean.values()) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean.values()) / len(clean)
    sigma = sqrt(variance)
    if sigma == 0:
        return dict(clean)
    low, high = mean - std * sigma, mean + std * sigma
    return {key: min(max(value, low), high) for key, value in clean.items()}


def group_neutralize(
    values: Mapping[str, float], groups: Mapping[str, str]
) -> dict[str, float]:
    """Subtract each symbol's group mean (BRAIN ``group_neutralize``).

    Symbols without a group are treated as a single residual bucket so the
    operator never silently drops names.
    """
    clean = _clean(values)
    buckets: dict[str, list[str]] = {}
    for key in clean:
        group = groups.get(key, "__ungrouped__")
        buckets.setdefault(group, []).append(key)
    out: dict[str, float] = {}
    for members in buckets.values():
        mean = sum(clean[key] for key in members) / len(members)
        for key in members:
            out[key] = clean[key] - mean
    return out


def group_rank(
    values: Mapping[str, float], groups: Mapping[str, str]
) -> dict[str, float]:
    """Rank within each group, normalised to ``[0, 1]``."""
    clean = _clean(values)
    buckets: dict[str, dict[str, float]] = {}
    for key, value in clean.items():
        group = groups.get(key, "__ungrouped__")
        buckets.setdefault(group, {})[key] = value
    out: dict[str, float] = {}
    for members in buckets.values():
        out.update(rank(members))
    return out


def truncate(values: Mapping[str, float], max_weight: float) -> dict[str, float]:
    """Cap any single absolute weight at ``max_weight`` of the gross book.

    Mirrors BRAIN's ``truncation`` simulation setting via water-filling: the unit
    gross budget is distributed proportionally to the original magnitudes, names
    that would exceed ``max_weight`` are frozen at the cap, and the remaining
    budget is redistributed.  When too few names are active to absorb the whole
    budget (``n_active * max_weight < 1``) every active name sits at the cap and
    the gross is intentionally below 1 rather than violating the cap.
    """
    if not 0 < max_weight <= 1:
        raise ValueError("max_weight must be in (0, 1]")
    scaled = scale(values, 1.0)
    magnitudes = {key: abs(value) for key, value in scaled.items() if abs(value) > 0}
    signs = {key: (1.0 if scaled[key] > 0 else -1.0) for key in magnitudes}
    frozen: dict[str, float] = {}
    active = set(magnitudes)
    remaining = 1.0

    while active:
        total = sum(magnitudes[key] for key in active)
        if total == 0:
            break
        overflow = {
            key for key in active if remaining * magnitudes[key] / total > max_weight + 1e-15
        }
        if not overflow:
            for key in active:
                frozen[key] = remaining * magnitudes[key] / total
            active.clear()
            break
        for key in overflow:
            frozen[key] = max_weight
            active.discard(key)
            remaining -= max_weight
        if remaining <= 0:
            break

    result = {key: 0.0 for key in scaled}
    for key, magnitude in frozen.items():
        result[key] = magnitude * signs[key]
    return result


# --------------------------------------------------------------------------- #
# Time-series operators
# --------------------------------------------------------------------------- #


def _window(series: Sequence[float], size: int, index: int) -> list[float] | None:
    if size <= 0:
        raise ValueError("window size must be positive")
    start = index - size + 1
    if start < 0:
        return None
    window = series[start : index + 1]
    if any(value is None for value in window):
        return None
    return [float(value) for value in window]


def ts_delay(series: Sequence[float], d: int) -> list[float | None]:
    """Value ``d`` steps ago (BRAIN ``ts_delay``)."""
    if d < 0:
        raise ValueError("d must be non-negative")
    return [series[i - d] if i - d >= 0 and series[i - d] is not None else None
            for i in range(len(series))]


def ts_delta(series: Sequence[float], d: int) -> list[float | None]:
    """``x[t] - x[t - d]`` (BRAIN ``ts_delta``)."""
    delayed = ts_delay(series, d)
    out: list[float | None] = []
    for i, value in enumerate(series):
        prior = delayed[i]
        out.append(None if value is None or prior is None else float(value) - prior)
    return out


def ts_sum(series: Sequence[float], d: int) -> list[float | None]:
    return [None if (w := _window(series, d, i)) is None else sum(w) for i in range(len(series))]


def ts_mean(series: Sequence[float], d: int) -> list[float | None]:
    return [None if (w := _window(series, d, i)) is None else sum(w) / d for i in range(len(series))]


def ts_stddev(series: Sequence[float], d: int) -> list[float | None]:
    """Trailing sample standard deviation (ddof=1)."""
    if d < 2:
        raise ValueError("ts_stddev needs d >= 2")
    out: list[float | None] = []
    for i in range(len(series)):
        window = _window(series, d, i)
        if window is None:
            out.append(None)
            continue
        mean = sum(window) / d
        variance = sum((value - mean) ** 2 for value in window) / (d - 1)
        out.append(sqrt(variance))
    return out


def ts_zscore(series: Sequence[float], d: int) -> list[float | None]:
    means = ts_mean(series, d)
    stds = ts_stddev(series, d)
    out: list[float | None] = []
    for i, value in enumerate(series):
        mean, std = means[i], stds[i]
        if value is None or mean is None or std is None or std == 0:
            out.append(None if std == 0 or value is None else 0.0)
        else:
            out.append((float(value) - mean) / std)
    return out


def ts_rank(series: Sequence[float], d: int) -> list[float | None]:
    """Rank of the current value within the trailing window, in ``[0, 1]``."""
    out: list[float | None] = []
    for i in range(len(series)):
        window = _window(series, d, i)
        if window is None:
            out.append(None)
            continue
        current = window[-1]
        if len(window) == 1:
            out.append(0.5)
            continue
        less = sum(1 for value in window if value < current)
        equal = sum(1 for value in window if value == current)
        average_rank = less + (equal - 1) / 2 + 1
        out.append((average_rank - 1) / (len(window) - 1))
    return out


def ts_decay_linear(series: Sequence[float], d: int) -> list[float | None]:
    """Linearly weighted moving average, most-recent weighted highest."""
    weights = list(range(1, d + 1))
    denom = sum(weights)
    out: list[float | None] = []
    for i in range(len(series)):
        window = _window(series, d, i)
        if window is None:
            out.append(None)
            continue
        out.append(sum(weight * value for weight, value in zip(weights, window)) / denom)
    return out


def ts_max(series: Sequence[float], d: int) -> list[float | None]:
    return [None if (w := _window(series, d, i)) is None else max(w) for i in range(len(series))]


def ts_min(series: Sequence[float], d: int) -> list[float | None]:
    return [None if (w := _window(series, d, i)) is None else min(w) for i in range(len(series))]
