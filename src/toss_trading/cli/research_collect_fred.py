from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.parse
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from toss_trading.research import DataLake
from toss_trading.research.providers import utc_now


FRED_OBSERVATIONS_URL = (
    "https://api.stlouisfed.org/fred/series/observations"
)
FRED_LICENSE_TAG = "fred-series-rights-reviewed-internal-research"


class FredObservationsClient:
    def __init__(
        self,
        api_key: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not api_key:
            raise ValueError("FRED API key is required")
        self.api_key = api_key
        self.opener = opener

    def fetch_revisions(
        self,
        series_id: str,
        *,
        realtime_start: str,
        realtime_end: str,
        observation_start: str,
    ) -> bytes:
        query = urllib.parse.urlencode(
            {
                "series_id": series_id,
                "api_key": self.api_key,
                "file_type": "json",
                "output_type": 3,
                "realtime_start": realtime_start,
                "realtime_end": realtime_end,
                "observation_start": observation_start,
                "limit": 100000,
                "sort_order": "asc",
            }
        )
        request = urllib.request.Request(
            f"{FRED_OBSERVATIONS_URL}?{query}",
            headers={"User-Agent": "toss-trading-core/0.1"},
            method="GET",
        )
        try:
            response = self.opener(request, timeout=30)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise RuntimeError(
                f"FRED request failed for series {series_id}"
            ) from None
        try:
            return response.read()
        finally:
            response.close()


def _approved_series(path: str) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    approved = []
    for row in rows:
        enabled = str(row.get("enabled", "")).strip().lower() == "true"
        rights_review = str(row.get("rights_review", "")).strip().lower()
        if enabled and rights_review != "approved":
            raise ValueError(
                f"enabled FRED series {row.get('series_id')} lacks approved rights review"
            )
        if enabled:
            approved.append(row)
    return approved


def build_parser() -> argparse.ArgumentParser:
    today = date.today()
    parser = argparse.ArgumentParser(
        description=(
            "Collect new and revised FRED/ALFRED observations for explicitly "
            "rights-approved series."
        )
    )
    parser.add_argument("--series-registry", default="config/fred_series.csv")
    parser.add_argument(
        "--realtime-start",
        default=(today - timedelta(days=90)).isoformat(),
    )
    parser.add_argument("--realtime-end", default=today.isoformat())
    parser.add_argument("--observation-start", default="2004-01-01")
    parser.add_argument("--output-root", default="research_data")
    parser.add_argument("--license-tag", default=FRED_LICENSE_TAG)
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION", "unknown"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    series = _approved_series(args.series_registry)
    if not series:
        raise RuntimeError(
            "no enabled FRED series has an approved series-level rights review"
        )
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        raise RuntimeError("FRED_API_KEY is missing")

    client = FredObservationsClient(api_key)
    lake = DataLake(args.output_root)
    manifests = []
    for item in series:
        series_id = item["series_id"].strip().upper()
        body = client.fetch_revisions(
            series_id,
            realtime_start=args.realtime_start,
            realtime_end=args.realtime_end,
            observation_start=args.observation_start,
        )
        payload = json.loads(body)
        if not isinstance(payload.get("observations"), list):
            raise ValueError(f"FRED response for {series_id} has no observations")
        retrieved = utc_now()
        manifests.append(
            lake.store_raw(
                source="fred-alfred",
                dataset="series-observation-revisions",
                body=body,
                media_type="application/json",
                schema_version="fred-observations-output-type-3-v1",
                available_at=retrieved,
                request={
                    "endpoint": FRED_OBSERVATIONS_URL,
                    "series_id": series_id,
                    "output_type": 3,
                    "realtime_start": args.realtime_start,
                    "realtime_end": args.realtime_end,
                    "observation_start": args.observation_start,
                },
                license_tag=args.license_tag,
                code_revision=args.code_revision,
                retrieved_at=retrieved,
            )
        )
    print(
        json.dumps(
            {
                "manifest_ids": [manifest.manifest_id for manifest in manifests],
                "objects": len(manifests),
                "series": [item["series_id"] for item in series],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
