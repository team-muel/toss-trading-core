from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path

from toss_trading.research import DataLake
from toss_trading.research.providers import (
    TIINGO_LICENSE_TAG,
    TiingoEodClient,
    ingest_tiingo_eod_response,
    utc_now,
)


def _universe_symbols(path: str) -> list[str]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return [
            row["symbol"].strip().upper()
            for row in csv.DictReader(handle)
            if row.get("symbol") and str(row.get("enabled", "true")).lower() == "true"
        ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect licensed Tiingo EOD raw and total-return daily history."
    )
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--universe")
    parser.add_argument("--start-date", default="2004-01-01")
    parser.add_argument(
        "--end-date",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Defaults to the previous calendar day to avoid an incomplete session.",
    )
    parser.add_argument("--output-root", default="research_data")
    parser.add_argument("--license-tag", default=TIINGO_LICENSE_TAG)
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
    canonical_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    if not canonical_symbols:
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
    for symbol in canonical_symbols:
        body = client.fetch(
            symbol,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        retrieved = utc_now()
        raw, normalized, rows = ingest_tiingo_eod_response(
            lake,
            symbol=symbol,
            body=body,
            start_date=args.start_date,
            end_date=args.end_date,
            retrieved_at=retrieved,
            code_revision=args.code_revision,
            license_tag=args.license_tag,
        )
        raw_manifest_ids.append(raw.manifest_id)
        normalized_manifest_ids.extend(
            manifest.manifest_id for manifest in normalized
        )
        row_count += rows
    print(
        json.dumps(
            {
                "normalized_manifest_ids": normalized_manifest_ids,
                "raw_manifest_ids": raw_manifest_ids,
                "rows": row_count,
                "symbols": canonical_symbols,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
