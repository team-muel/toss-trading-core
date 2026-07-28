from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

from toss_trading.broker.credentials import load_toss_credentials_from_env
from toss_trading.broker.toss import TossApiError, TossReadOnlyAdapter
from toss_trading.research import DataLake
from toss_trading.research.providers import TOSS_LICENSE_TAG, utc_now


MARKET_INDICATORS = (
    "KOSPI",
    "KOSDAQ",
    "KR_BOND_2Y",
    "KR_BOND_3Y",
    "KR_BOND_5Y",
    "KR_BOND_10Y",
    "KR_BOND_20Y",
    "KR_BOND_30Y",
)
RANKING_TYPES = (
    "MARKET_TRADING_AMOUNT",
    "MARKET_TRADING_VOLUME",
    "TOP_GAINERS",
    "TOP_LOSERS",
    "TOSS_SECURITIES_TRADING_AMOUNT",
    "TOSS_SECURITIES_TRADING_VOLUME",
)


def _universe_symbols(path: str) -> list[str]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return sorted(
            {
                row["symbol"].strip().upper()
                for row in csv.DictReader(handle)
                if row.get("symbol")
                and str(row.get("enabled", "true")).lower() == "true"
            }
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect useful read-only Toss reference, calendar, ranking, FX, "
            "and market-indicator snapshots into the immutable research lake."
        )
    )
    parser.add_argument("--universe", default="data/universe.csv")
    parser.add_argument("--output-root", default="research_data")
    parser.add_argument("--market-country", action="append", default=["US"])
    parser.add_argument("--ranking-duration", default="1d")
    parser.add_argument("--ranking-count", type=int, default=100)
    parser.add_argument("--indicator-candle-count", type=int, default=200)
    parser.add_argument("--license-tag", default=TOSS_LICENSE_TAG)
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    return parser


def _store(
    lake: DataLake,
    *,
    dataset: str,
    body: Any,
    request: dict[str, Any],
    retrieved_at: str,
    code_revision: str,
    license_tag: str,
):
    return lake.store_raw(
        source="toss-openapi",
        dataset=dataset,
        body=body,
        media_type="application/json",
        schema_version=f"{dataset}-v1",
        available_at=retrieved_at,
        request=request,
        license_tag=license_tag,
        code_revision=code_revision,
        retrieved_at=retrieved_at,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    symbols = _universe_symbols(args.universe)
    if not symbols:
        raise ValueError("enabled Toss reference universe is empty")
    markets = sorted({market.upper() for market in args.market_country})
    invalid_markets = set(markets) - {"KR", "US"}
    if invalid_markets:
        raise ValueError(f"unsupported market countries: {sorted(invalid_markets)}")

    adapter = TossReadOnlyAdapter(load_toss_credentials_from_env())
    lake = DataLake(args.output_root)
    manifests = []
    failures: list[dict[str, Any]] = []

    def store_result(dataset: str, result: Any, request: dict[str, Any]) -> None:
        retrieved = utc_now()
        manifests.append(
            _store(
                lake,
                dataset=dataset,
                body=result.body,
                request=request,
                retrieved_at=retrieved,
                code_revision=args.code_revision,
                license_tag=args.license_tag,
            )
        )

    def store_resilient_batch(
        dataset: str,
        endpoint: str,
        batch: list[str],
        method: Any,
    ) -> None:
        try:
            result = method(batch)
        except TossApiError as exc:
            if (
                exc.status_code == 404
                and exc.error.get("code") == "stock-not-found"
            ):
                if len(batch) == 1:
                    failures.append(
                        {
                            "dataset": dataset,
                            "endpoint": endpoint,
                            "symbol": batch[0],
                            "status_code": exc.status_code,
                            "code": exc.error.get("code"),
                            "reason": "provider_symbol_unavailable",
                        }
                    )
                    return
                midpoint = len(batch) // 2
                store_resilient_batch(
                    dataset,
                    endpoint,
                    batch[:midpoint],
                    method,
                )
                store_resilient_batch(
                    dataset,
                    endpoint,
                    batch[midpoint:],
                    method,
                )
                return
            raise
        store_result(
            dataset,
            result,
            {"endpoint": endpoint, "symbols": batch},
        )

    store_resilient_batch(
        "stock-reference",
        "/api/v1/stocks",
        symbols,
        adapter.get_stocks,
    )
    store_resilient_batch(
        "current-prices",
        "/api/v1/prices",
        symbols,
        adapter.get_prices,
    )
    for symbol in symbols:
        try:
            result = adapter.get_stock_warnings(symbol)
        except TossApiError as exc:
            if (
                exc.status_code == 404
                and exc.error.get("code") == "stock-not-found"
            ):
                failures.append(
                    {
                        "dataset": "stock-warnings",
                        "endpoint": f"/api/v1/stocks/{symbol}/warnings",
                        "symbol": symbol,
                        "status_code": exc.status_code,
                        "code": exc.error.get("code"),
                        "reason": "provider_symbol_unavailable",
                    }
                )
                continue
            raise
        store_result(
            "stock-warnings",
            result,
            {
                "endpoint": f"/api/v1/stocks/{symbol}/warnings",
                "symbol": symbol,
            },
        )

    store_result(
        "exchange-rate",
        adapter.get_exchange_rate(
            base_currency="USD",
            quote_currency="KRW",
        ),
        {
            "endpoint": "/api/v1/exchange-rate",
            "baseCurrency": "USD",
            "quoteCurrency": "KRW",
        },
    )
    for market in markets:
        store_result(
            "market-calendar",
            adapter.get_market_calendar(market),
            {
                "endpoint": f"/api/v1/market-calendar/{market}",
                "marketCountry": market,
            },
        )
        for ranking_type in RANKING_TYPES:
            store_result(
                "rankings",
                adapter.get_rankings(
                    ranking_type=ranking_type,
                    market_country=market,
                    duration=args.ranking_duration,
                    count=args.ranking_count,
                ),
                {
                    "endpoint": "/api/v1/rankings",
                    "type": ranking_type,
                    "marketCountry": market,
                    "duration": args.ranking_duration,
                    "count": args.ranking_count,
                    "excludeInvestmentCaution": True,
                },
            )

    store_result(
        "market-indicator-prices",
        adapter.get_market_indicator_prices(list(MARKET_INDICATORS)),
        {
            "endpoint": "/api/v1/market-indicators/prices",
            "symbols": list(MARKET_INDICATORS),
        },
    )
    for symbol in MARKET_INDICATORS:
        store_result(
            "market-indicator-candles",
            adapter.get_market_indicator_candles(
                symbol,
                interval="1d",
                count=args.indicator_candle_count,
            ),
            {
                "endpoint": f"/api/v1/market-indicators/{symbol}/candles",
                "symbol": symbol,
                "interval": "1d",
                "count": args.indicator_candle_count,
            },
        )
    for symbol in ("KOSPI", "KOSDAQ"):
        store_result(
            "market-indicator-investor-trading",
            adapter.get_market_indicator_investor_trading(
                symbol,
                interval="1d",
                count=100,
            ),
            {
                "endpoint": (
                    f"/api/v1/market-indicators/{symbol}/investor-trading"
                ),
                "symbol": symbol,
                "interval": "1d",
                "count": 100,
            },
        )

    if failures:
        retrieved = utc_now()
        manifests.append(
            _store(
                lake,
                dataset="reference-collection-failures",
                body={"failures": failures},
                request={"endpoints": "reference", "symbols": symbols},
                retrieved_at=retrieved,
                code_revision=args.code_revision,
                license_tag=args.license_tag,
            )
        )

    print(
        json.dumps(
            {
                "datasets": sorted({manifest.dataset for manifest in manifests}),
                "manifest_ids": [manifest.manifest_id for manifest in manifests],
                "objects": len(manifests),
                "symbols": symbols,
                "failures": failures,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
