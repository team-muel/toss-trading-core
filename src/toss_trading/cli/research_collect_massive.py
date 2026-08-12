from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from toss_trading.research.massive import (
    collect_common_stock_reference,
    collect_grouped_daily_history,
)


def _write_json(path: str | Path, payload: object) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect gated Massive broad-stock data.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    reference = subparsers.add_parser("reference")
    reference.add_argument("--output", required=True)
    grouped = subparsers.add_parser("grouped")
    grouped.add_argument("--reference", required=True)
    grouped.add_argument("--start-date", required=True)
    grouped.add_argument("--through-date", required=True)
    grouped.add_argument("--raw-directory", required=True)
    grouped.add_argument("--output-jsonl", required=True)
    grouped.add_argument("--summary", required=True)
    grouped.add_argument("--request-interval-seconds", type=float, default=12.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.environ.get("RESEARCH_MASSIVE_PERSONAL_TERMS_APPROVED") != "1":
        raise ValueError("Massive personal terms approval gate is closed")
    api_key = os.environ.get("MASSIVE_API_KEY", "")
    if not api_key:
        raise ValueError("MASSIVE_API_KEY is required")
    if args.command == "reference":
        payload = collect_common_stock_reference(api_key)
        _write_json(args.output, payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
    allowed = {
        str(item["symbol"])
        for item in reference.get("symbols") or []
        if item.get("active") is True and item.get("type") == "CS"
    }
    rows, summary = collect_grouped_daily_history(
        api_key,
        start_date=args.start_date,
        through_date=args.through_date,
        allowed_symbols=allowed,
        raw_directory=args.raw_directory,
        request_interval_seconds=args.request_interval_seconds,
    )
    destination = Path(args.output_jsonl)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    _write_json(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
