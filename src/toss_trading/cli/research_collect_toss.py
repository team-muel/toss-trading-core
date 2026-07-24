from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from toss_trading.broker.credentials import load_toss_credentials_from_env
from toss_trading.broker.toss import TossReadOnlyAdapter
from toss_trading.research import DataLake
from toss_trading.research.providers import (
    TOSS_LICENSE_TAG,
    collect_toss_candle_bundle,
    ingest_toss_candle_bundle,
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
        description="Collect or ingest read-only Toss daily-candle history."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser(
        "collect",
        help="Fetch raw pages into a portable JSON bundle; no order endpoint is called.",
    )
    collect.add_argument("--output", required=True)
    collect.add_argument("--symbol", action="append", default=[])
    collect.add_argument("--universe")
    collect.add_argument("--start-date", default="2004-01-01")
    collect.add_argument("--count", type=int, default=200)
    collect.add_argument("--max-pages", type=int, default=100)
    collect.add_argument(
        "--skip-unavailable-symbols",
        action="store_true",
        help="Continue only when Toss explicitly returns 404 stock-not-found.",
    )
    adjustment = collect.add_mutually_exclusive_group(required=True)
    adjustment.add_argument("--adjusted", action="store_true")
    adjustment.add_argument("--raw", action="store_true")

    ingest = subparsers.add_parser(
        "ingest",
        help="Store a collected bundle as immutable JSON and normalized Parquet.",
    )
    ingest.add_argument("--input", required=True)
    ingest.add_argument("--output-root", default="research_data")
    ingest.add_argument(
        "--through-date",
        help="Last completed exchange session to include in normalized bars.",
    )
    ingest.add_argument("--license-tag", default=TOSS_LICENSE_TAG)
    ingest.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect":
        symbols = list(args.symbol)
        if args.universe:
            symbols.extend(_universe_symbols(args.universe))
        adapter = TossReadOnlyAdapter(load_toss_credentials_from_env())
        bundle = collect_toss_candle_bundle(
            adapter,
            symbols=symbols,
            start_date=args.start_date,
            adjusted=args.adjusted,
            count=args.count,
            max_pages=args.max_pages,
            skip_unavailable_symbols=args.skip_unavailable_symbols,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "adjusted": args.adjusted,
                    "bundle": str(output),
                    "failures": bundle["failures"],
                    "pages": len(bundle["pages"]),
                    "symbols": len(bundle["request"]["symbols"]),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    bundle = json.loads(Path(args.input).read_text(encoding="utf-8"))
    raw, normalized, rows = ingest_toss_candle_bundle(
        DataLake(args.output_root),
        bundle,
        code_revision=args.code_revision,
        license_tag=args.license_tag,
        through_date=args.through_date,
    )
    print(
        json.dumps(
            {
                "raw_manifest_ids": [manifest.manifest_id for manifest in raw],
                "normalized_manifest_ids": [
                    manifest.manifest_id for manifest in normalized
                ],
                "rows": rows,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
