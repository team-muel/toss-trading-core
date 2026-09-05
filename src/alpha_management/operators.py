"""WorldQuant BRAIN-style alpha operators.

Cross-sectional operators consume instrument->value mappings. Time-series
operators consume oldest-to-newest sequences and return `None` during warm-up.
This module is research-only and has no broker or execution dependency.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import sqrt

__all__ = [
    "rank", "zscore", "scale", "sign", "winsorize", "group_neutralize",
    "group_rank", "truncate", "ts_delay", "ts_delta", "ts_sum", "ts_mean",
    "ts_stddev", "ts_zscore", "ts_rank", "ts_decay_linear", "ts_max", "ts_min",
]


def _clean(values: Mapping[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in values.items():
        if value is None:
            continue
        number = float(value)
        if number != number:
            continue
        out[key] = number
    return out


def _average_ranks(values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: item[1])
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        stop = index
        while stop + 1 < len(ordered) and ordered[stop + 1][1] == ordered[index][1]:
            stop += 1
        average = (index + stop) / 2 + 1
        for pos in range(index, stop + 1):
            ranks[ordered[pos][0]] = average
        index = stop + 1
    return ranks


def rank(values: Mapping[str, float]) -> dict[str, float]:
    clean = _clean(values)
    if not clean:
        return {}
    if len(clean) == 1:
        return {next(iter(clean)): 0.5}
    ranks = _average_ranks(clean)
    n = len(clean)
    return {key: (value - 1) / (n - 1) for key, value in ranks.items()}


def zscore(values: Mapping[str, float]) -> dict[str, float]:
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


def group_neutralize(values: Mapping[str, float], groups: Mapping[str, str]) -> dict[str, float]:
    clean = _clean(values)
    buckets: dict[str, list[str]] = {}
    for key in clean:
        buckets.setdefault(groups.get(key, "__ungrouped__"), []).append(key)
    out: dict[str, float] = {}
    for members in buckets.values():
        mean = sum(clean[key] for key in members) / len(members)
        for key in members:
            out[key] = clean[key] - mean
    return out


def group_rank(values: Mapping[str, float], groups: Mapping[str, str]) -> dict[str, float]:
    clean = _clean(values)
    buckets: dict[str, dict[str, float]] = {}
    for key, value in clean.items():
        buckets.setdefault(groups.get(key, "__ungrouped__"), {})[key] = value
    out: dict[str, float] = {}
    for members in buckets.values():
        out.update(rank(members))
    return out


def truncate(values: Mapping[str, float], max_weight: float) -> dict[str, float]:
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
            key for key in active
            if remaining * magnitudes[key] / total > max_weight + 1e-15
        }
        if not overflow:
            for key in active:
                frozen[key] = remaining * magnitudes[key] / total
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


def _window(series: Sequence[float], size: int, index: int) -> list[float] | None:
    if size <= 0:
        raise ValueError("window size must be positive")
    start = index - size + 1
    if start < 0:
        return None
    window = series[start:index + 1]
    if any(value is None for value in window):
        return None
    return [float(value) for value in window]


def ts_delay(series: Sequence[float], d: int) -> list[float | None]:
    if d < 0:
        raise ValueError("d must be non-negative")
    return [series[i - d] if i - d >= 0 and series[i - d] is not None else None for i in range(len(series))]


def ts_delta(series: Sequence[float], d: int) -> list[float | None]:
    delayed = ts_delay(series, d)
    return [None if value is None or delayed[i] is None else float(value) - float(delayed[i]) for i, value in enumerate(series)]


def ts_sum(series: Sequence[float], d: int) -> list[float | None]:
    return [None if (w := _window(series, d, i)) is None else sum(w) for i in range(len(series))]


def ts_mean(series: Sequence[float], d: int) -> list[float | None]:
    return [None if (w := _window(series, d, i)) is None else sum(w) / d for i in range(len(series))]


def ts_stddev(series: Sequence[float], d: int) -> list[float | None]:
    if d < 2:
        raise ValueError("ts_stddev needs d >= 2")
    out: list[float | None] = []
    for i in range(len(series)):
        window = _window(series, d, i)
        if window is None:
            out.append(None)
            continue
        mean = sum(window) / d
        out.append(sqrt(sum((value - mean) ** 2 for value in window) / (d - 1)))
    return out


def ts_zscore(series: Sequence[float], d: int) -> list[float | None]:
    means = ts_mean(series, d)
    stds = ts_stddev(series, d)
    out: list[float | None] = []
    for i, value in enumerate(series):
        mean, std = means[i], stds[i]
        if value is None or mean is None or std is None:
            out.append(None)
        elif std == 0:
            out.append(0.0)
        else:
            out.append((float(value) - mean) / std)
    return out


def ts_rank(series: Sequence[float], d: int) -> list[float | None]:
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
    if d <= 0:
        raise ValueError("d must be positive")
    weights = list(range(1, d + 1))
    denom = sum(weights)
    out: list[float | None] = []
    for i in range(len(series)):
        window = _window(series, d, i)
        out.append(None if window is None else sum(weight * value for weight, value in zip(weights, window)) / denom)
    return out


def ts_max(series: Sequence[float], d: int) -> list[float | None]:
    return [None if (w := _window(series, d, i)) is None else max(w) for i in range(len(series))]


def ts_min(series: Sequence[float], d: int) -> list[float | None]:
    return [None if (w := _window(series, d, i)) is None else min(w) for i in range(len(series))]
