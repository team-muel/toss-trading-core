from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from toss_trading.data.universe import load_instrument_mappings
from toss_trading.research import DataLake
from toss_trading.research.providers import (
    TIINGO_LICENSE_TAG,
    TiingoEodClient,
    ingest_tiingo_eod_response,
    utc_now,
)
from toss_trading.research.prospective import append_collection_observation


def _universe_symbols(path: str) -> list[str]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return [
            row["symbol"].strip().upper()
            for row in csv.DictReader(handle)
            if row.get("symbol") and str(row.get("enabled", "true")).lower() == "true"
        ]


def _provider_requests(
    symbols: list[str],
    instrument_master: str | None,
) -> dict[str, str]:
    canonical = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    if not instrument_master:
        return {symbol: symbol for symbol in canonical}
    mappings = {
        item.ticker: item.vendor_symbol
        for item in load_instrument_mappings(instrument_master)
    }
    missing = sorted(set(canonical) - set(mappings))
    if missing:
        raise ValueError(f"Tiingo symbols lack instrument mappings: {missing}")
    return {symbol: mappings[symbol] for symbol in canonical}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect licensed Tiingo EOD raw and total-return daily history."
    )
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--universe")
    parser.add_argument(
        "--instrument-master",
        help=(
            "Resolve canonical research symbols to provider aliases. This is "
            "required by automation so ticker changes remain point-in-time auditable."
        ),
    )
    parser.add_argument("--start-date", default="2004-01-01")
    parser.add_argument(
        "--end-date",
        default=(datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat(),
        help=(
            "Defaults to the previous UTC calendar day so local timezones cannot "
            "include an incomplete US session."
        ),
    )
    parser.add_argument("--output-root", default="research_data")
    parser.add_argument("--license-tag", default=TIINGO_LICENSE_TAG)
    parser.add_argument("--observation-ledger")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    symbols = list(args.symbol)
    if args.universe:
        symbols.extend(_universe_symbols(args.universe))
    requests = _provider_requests(symbols, args.instrument_master)
    canonical_symbols = sorted(requests)
    if not requests:
        raise ValueError("at least one Tiingo symbol is required")
    token = os.environ.get("TIINGO_API_TOKEN", "")
    if not token:
        raise RuntimeError(
            "TIINGO_API_TOKEN is missing; create a Tiingo account, accept the selected "
            "Internal Use Only license, and load the token from Secret Manager or the environment"
        )
    client = TiingoEodClient(token)
    lake = DataLake(args.output_root)
    raw_manifest_ids: list[str] = []
    normalized_manifest_ids: list[str] = []
    row_count = 0
    latest_dates_by_symbol: dict[str, str] = {}
    retrieved_times: list[str] = []
    provider_symbols: dict[str, str] = {}
    for symbol in canonical_symbols:
        provider_symbol = requests[symbol]
        provider_symbols[symbol] = provider_symbol
        body = client.fetch(
            provider_symbol,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        retrieved = utc_now()
        retrieved_times.append(retrieved)
        parsed = json.loads(body)
        response_dates = [
            str(item.get("date", ""))[:10]
            for item in parsed
            if isinstance(item, dict) and item.get("date")
        ]
        if not response_dates:
            raise ValueError(f"Tiingo returned no dated EOD rows for {symbol}")
        latest_dates_by_symbol[symbol] = max(response_dates)
        raw, normalized, rows = ingest_tiingo_eod_response(
            lake,
            symbol=symbol,
            body=body,
            start_date=args.start_date,
            end_date=args.end_date,
            retrieved_at=retrieved,
            code_revision=args.code_revision,
            license_tag=args.license_tag,
            provider_symbol=provider_symbol,
        )
        raw_manifest_ids.append(raw.manifest_id)
        normalized_manifest_ids.extend(
            manifest.manifest_id for manifest in normalized
        )
        row_count += rows
    observation = None
    if args.observation_ledger:
        if not args.run_id:
            raise ValueError("--run-id is required with --observation-ledger")
        observation = append_collection_observation(
            args.observation_ledger,
            provider="tiingo-eod",
            adjustment="total_return",
            run_id=args.run_id,
            code_revision=args.code_revision,
            requested_through_date=args.end_date,
            collected_at=max(retrieved_times),
            latest_dates_by_symbol=latest_dates_by_symbol,
        )
    print(
        json.dumps(
            {
                "normalized_manifest_ids": normalized_manifest_ids,
                "raw_manifest_ids": raw_manifest_ids,
                "rows": row_count,
                "symbols": canonical_symbols,
                "provider_symbols": provider_symbols,
                "complete_through_date": min(latest_dates_by_symbol.values()),
                "observation_id": (
                    observation.get("observation_id") if observation else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
