from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from toss_trading.research import DataLake
from toss_trading.research.providers import (
    SEC_LICENSE_TAG,
    SecEdgarClient,
    collect_sec_reference_data,
)


def _instrument_ciks(path: str) -> list[str]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return [
            row["cik"].strip()
            for row in csv.DictReader(handle)
            if row.get("cik") and not row.get("effective_to")
        ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect SEC ticker mapping and issuer submission history."
    )
    parser.add_argument("--cik", action="append", default=[])
    parser.add_argument("--instrument-master")
    parser.add_argument("--output-root", default="research_data")
    parser.add_argument(
        "--include-companyfacts",
        action="store_true",
        help="Also retain issuer XBRL company facts for approved research.",
    )
    parser.add_argument("--license-tag", default=SEC_LICENSE_TAG)
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", ""),
        help="Descriptive SEC-compliant User-Agent; SEC_USER_AGENT is preferred.",
    )
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ciks = list(args.cik)
    if args.instrument_master:
        ciks.extend(_instrument_ciks(args.instrument_master))
    if not ciks:
        raise ValueError("at least one CIK is required")
    if not args.user_agent:
        raise RuntimeError(
            "SEC_USER_AGENT is required and must identify the application and a contact"
        )
    manifests = collect_sec_reference_data(
        DataLake(args.output_root),
        SecEdgarClient(args.user_agent),
        ciks=ciks,
        code_revision=args.code_revision,
        license_tag=args.license_tag,
        include_companyfacts=args.include_companyfacts,
    )
    print(
        json.dumps(
            {
                "manifest_ids": [manifest.manifest_id for manifest in manifests],
                "objects": len(manifests),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
