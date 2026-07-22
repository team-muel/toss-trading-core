"""Toss market-data datafields (research decision inputs, read-only).

Wires the read-only Toss candle/price endpoints into the alpha layer.  Toss is
the execution/account source of truth; here its *market* reads are used only as
decision inputs to build datafields (momentum, close panels).  Nothing in this
module places orders or mutates account state.

The reader is a plain ``Callable[[str], list[dict]]`` returning a symbol's
candles oldest-to-newest, so backtests and tests inject synthetic candles and
run fully offline.  ``TossCandleReader`` is the default, backed by
``TossReadOnlyAdapter.get_candles`` — credentials come from the environment
(see the Notion "Toss API" page → ``TOSS_CLIENT_ID`` / ``TOSS_CLIENT_SECRET``),
never from source or checked-in config.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

# reader(symbol) -> list of candle dicts, oldest first
CandleReader = Callable[[str], Sequence[Mapping[str, object]]]


def extract_candles(body: object) -> list[dict]:
    """Pull the candle array out of a Toss ``/candles`` response body."""
    result = body.get("result") if isinstance(body, Mapping) else None
    container = result if isinstance(result, Mapping) else body
    candles = container.get("candles") if isinstance(container, Mapping) else None
    if not isinstance(candles, Sequence):
        return []
    out = [dict(candle) for candle in candles if isinstance(candle, Mapping)]
    # Toss paginates backward via `before`; normalise to chronological order.
    out.sort(key=lambda candle: str(candle.get("timestamp", "")))
    return out


def closes(candles: Sequence[Mapping[str, object]]) -> list[float]:
    """Chronological close prices from candle dicts."""
    values: list[float] = []
    for candle in candles:
        price = candle.get("closePrice")
        if price is None:
            continue
        try:
            values.append(float(price))
        except (TypeError, ValueError):
            continue
    return values


@dataclass(frozen=True)
class TossCandleReader:
    """Default reader backed by a read-only Toss adapter.

    ``adapter`` only needs a ``get_candles(symbol, interval=, count=)`` method
    whose result exposes ``.body`` (``TossReadOnlyAdapter`` satisfies this).
    """

    adapter: object
    interval: str = "1d"
    count: int = 120

    def __call__(self, symbol: str) -> list[dict]:
        result = self.adapter.get_candles(  # type: ignore[attr-defined]
            symbol, interval=self.interval, count=self.count
        )
        return extract_candles(getattr(result, "body", result))


def close_panel(symbols: Sequence[str], reader: CandleReader) -> dict[str, list[float]]:
    """Map each symbol to its chronological close series (empty series dropped)."""
    panel: dict[str, list[float]] = {}
    for symbol in symbols:
        series = closes(reader(symbol))
        if series:
            panel[symbol] = series
    return panel


def momentum_datafield(
    symbols: Sequence[str], reader: CandleReader, *, lookback: int
) -> dict[str, float]:
    """Trailing simple return over ``lookback`` periods: ``close[-1]/close[-1-lookback] - 1``.

    Symbols without at least ``lookback + 1`` closes (or a zero base price) are
    skipped so a thin-history name never becomes a fake 0 signal.
    """
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    field: dict[str, float] = {}
    for symbol in symbols:
        series = closes(reader(symbol))
        if len(series) < lookback + 1:
            continue
        base = series[-1 - lookback]
        if base == 0:
            continue
        field[symbol] = series[-1] / base - 1.0
    return field


def forward_returns_panel(close_series: Mapping[str, Sequence[float]]) -> dict[str, list[float]]:
    """Per-period simple returns aligned so ``r[t]`` is the return into ``t+1``.

    Useful for feeding ``metrics.daily_pnl`` in an offline backtest.  The last
    element of each series is 0.0 because there is no realised next period.
    """
    panel: dict[str, list[float]] = {}
    for symbol, series in close_series.items():
        values = [float(price) for price in series]
        returns: list[float] = []
        for index in range(len(values)):
            if index + 1 >= len(values) or values[index] == 0:
                returns.append(0.0)
            else:
                returns.append(values[index + 1] / values[index] - 1.0)
        panel[symbol] = returns
    return panel
