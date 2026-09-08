"""Deterministic market feature transformations."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Mapping, Sequence

from asset_management.domain.errors import DataQualityError


def _numbers(values: Sequence[object], minimum: int) -> tuple[Decimal, ...]:
    if len(values) < minimum:
        raise DataQualityError("MISSING_HISTORY")
    try:
        output = tuple(Decimal(str(value)) for value in values)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataQualityError("FEATURE_INPUT_INVALID") from exc
    if any(not item.is_finite() for item in output):
        raise DataQualityError("FEATURE_INPUT_INVALID")
    return output


def period_return(prices: Sequence[object], periods: int) -> Decimal:
    if periods < 1:
        raise ValueError("FEATURE_PARAMETER_INVALID")
    values = _numbers(prices, periods + 1)
    start, end = values[-periods - 1], values[-1]
    if start <= 0:
        raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
    return end / start - Decimal(1)


def momentum_12_1(monthly_prices: Sequence[object]) -> Decimal:
    values = _numbers(monthly_prices, 13)
    if values[-13] <= 0:
        raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
    return values[-2] / values[-13] - Decimal(1)


def realized_volatility(prices: Sequence[object], window: int, *, annualization: int = 252) -> Decimal:
    if window < 1 or annualization < 1:
        raise ValueError("FEATURE_PARAMETER_INVALID")
    values = _numbers(prices, window + 1)[-window - 1:]
    if any(item <= 0 for item in values):
        raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
    returns = tuple(values[index] / values[index - 1] - 1 for index in range(1, len(values)))
    mean = sum(returns) / Decimal(len(returns))
    variance = sum((item - mean) ** 2 for item in returns) / Decimal(len(returns))
    with localcontext() as context:
        context.prec = 34
        return variance.sqrt() * Decimal(annualization).sqrt()


def drawdown_from_high(prices: Sequence[object], window: int | None = None) -> Decimal:
    values = _numbers(prices, window or 1)
    selected = values[-window:] if window else values
    high = max(selected)
    if high <= 0:
        raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
    return selected[-1] / high - 1


def moving_average_distance(prices: Sequence[object], window: int) -> Decimal:
    if window < 1:
        raise ValueError("FEATURE_PARAMETER_INVALID")
    values = _numbers(prices, window)[-window:]
    average = sum(values) / Decimal(window)
    if average <= 0:
        raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
    return values[-1] / average - 1


def volume_trend(volumes: Sequence[object], *, short_window: int = 20, long_window: int = 60) -> Decimal:
    if short_window < 1 or long_window < 1 or short_window > long_window:
        raise ValueError("FEATURE_PARAMETER_INVALID")
    values = _numbers(volumes, long_window)[-long_window:]
    if any(item < 0 for item in values):
        raise DataQualityError("FEATURE_INPUT_INVALID")
    long_mean = sum(values) / Decimal(long_window)
    if long_mean == 0:
        raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
    short_mean = sum(values[-short_window:]) / Decimal(short_window)
    return short_mean / long_mean - 1


def market_breadth(histories: Mapping[str, Sequence[object]], *, window: int) -> Decimal:
    if window < 1:
        raise ValueError("FEATURE_PARAMETER_INVALID")
    if not histories:
        raise DataQualityError("MISSING_HISTORY")
    above = 0
    for prices in histories.values():
        values = _numbers(prices, window)[-window:]
        average = sum(values) / Decimal(window)
        if average <= 0:
            raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
        above += values[-1] > average
    return Decimal(above) / Decimal(len(histories))


def trend_slope(values: Sequence[object], window: int) -> Decimal:
    if window < 2:
        raise ValueError("FEATURE_PARAMETER_INVALID")
    selected = _numbers(values, window)[-window:]
    if selected[0] == 0:
        raise DataQualityError("FEATURE_DENOMINATOR_INVALID")
    return (selected[-1] / selected[0] - 1) / Decimal(window - 1)


def credit_spread(corporate_yield: object, risk_free_yield: object) -> Decimal:
    try:
        return Decimal(str(corporate_yield)) - Decimal(str(risk_free_yield))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataQualityError("FEATURE_INPUT_INVALID") from exc
