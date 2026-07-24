from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from toss_trading.research import DataLake, MarketBar


REQUIRED_COLUMNS = {
    "symbol",
    "event_time_utc",
    "available_at",
    "exchange_local_date",
    "interval",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "currency",
    "session",
    "adjustment",
    "source_revision",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store an immutable raw CSV and normalized market-bar Parquet."
    )
    parser.add_argument("--input", required=True, help="Provider-exported market-bar CSV.")
    parser.add_argument("--source", required=True, help="Canonical provider name.")
    parser.add_argument("--dataset", default="market-bars")
    parser.add_argument("--schema-version", default="provider-csv-v1")
    parser.add_argument("--license-tag", required=True)
    parser.add_argument("--available-at", required=True)
    parser.add_argument("--output-root", default="research_data")
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input)
    payload = input_path.read_bytes()
    lake = DataLake(args.output_root)
    raw = lake.store_raw(
        source=args.source,
        dataset=args.dataset,
        body=payload,
        media_type="text/csv",
        schema_version=args.schema_version,
        available_at=args.available_at,
        request={"input_filename": input_path.name},
        license_tag=args.license_tag,
        code_revision=args.code_revision,
    )
    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"market-bar CSV missing columns: {sorted(missing)}")
        rows = [
            MarketBar(
                symbol=row["symbol"].strip().upper(),
                event_time_utc=row["event_time_utc"].strip(),
                available_at=row["available_at"].strip(),
                exchange_local_date=row["exchange_local_date"].strip(),
                interval=row["interval"].strip(),
                open=row["open"].strip(),
                high=row["high"].strip(),
                low=row["low"].strip(),
                close=row["close"].strip(),
                volume=row["volume"].strip(),
                currency=row["currency"].strip().upper(),
                session=row["session"].strip(),
                adjustment=row["adjustment"].strip(),
                source=args.source,
                source_revision=row["source_revision"].strip(),
                raw_manifest_id=raw.manifest_id,
                quality_flag=(row.get("quality_flag") or "ok").strip(),
            )
            for row in reader
        ]
    normalized = lake.write_market_bars(
        rows,
        code_revision=args.code_revision,
        license_tag=args.license_tag,
    )
    print(
        json.dumps(
            {
                "raw_manifest_id": raw.manifest_id,
                "normalized_manifest_ids": [
                    manifest.manifest_id for manifest in normalized
                ],
                "rows": len(rows),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
