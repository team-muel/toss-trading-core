from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import time
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
# Three-year vintage windows avoid FRED gateway timeouts observed on some
# high-frequency series while staying comfortably below the 2,000-vintage cap.
MAX_REALTIME_WINDOW_DAYS = 1095
FRED_REQUEST_TIMEOUT_SECONDS = 90
FRED_MAX_ATTEMPTS = 3
FRED_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


class FredObservationsClient:
    def __init__(
        self,
        api_key: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout_seconds: int = FRED_REQUEST_TIMEOUT_SECONDS,
        max_attempts: int = FRED_MAX_ATTEMPTS,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("FRED API key is required")
        if timeout_seconds <= 0:
            raise ValueError("FRED timeout must be positive")
        if max_attempts <= 0:
            raise ValueError("FRED max_attempts must be positive")
        self.api_key = api_key
        self.opener = opener
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.sleeper = sleeper

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
        for attempt in range(1, self.max_attempts + 1):
            response = None
            try:
                response = self.opener(
                    request,
                    timeout=self.timeout_seconds,
                )
                return response.read()
            except urllib.error.HTTPError as exc:
                if (
                    exc.code not in FRED_RETRYABLE_HTTP_STATUSES
                    or attempt == self.max_attempts
                ):
                    raise RuntimeError(
                        f"FRED request failed for series {series_id} "
                        f"with HTTP status {exc.code}"
                    ) from None
            except (
                urllib.error.URLError,
                TimeoutError,
                socket.timeout,
                ConnectionError,
            ):
                if attempt == self.max_attempts:
                    raise RuntimeError(
                        f"FRED request failed for series {series_id} "
                        f"after {self.max_attempts} network attempts"
                    ) from None
            finally:
                if response is not None:
                    response.close()
            self.sleeper(float(2 ** (attempt - 1)))
        raise AssertionError("unreachable FRED retry state")


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


def _realtime_windows(
    realtime_start: str,
    realtime_end: str,
) -> list[tuple[str, str]]:
    start = date.fromisoformat(realtime_start)
    end = date.fromisoformat(realtime_end)
    if end < start:
        raise ValueError("FRED realtime_end precedes realtime_start")
    windows = []
    cursor = start
    while cursor <= end:
        window_end = min(
            cursor + timedelta(days=MAX_REALTIME_WINDOW_DAYS - 1),
            end,
        )
        windows.append((cursor.isoformat(), window_end.isoformat()))
        cursor = window_end + timedelta(days=1)
    return windows


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
    windows = _realtime_windows(args.realtime_start, args.realtime_end)
    for item in series:
        series_id = item["series_id"].strip().upper()
        for realtime_start, realtime_end in windows:
            body = client.fetch_revisions(
                series_id,
                realtime_start=realtime_start,
                realtime_end=realtime_end,
                observation_start=args.observation_start,
            )
            payload = json.loads(body)
            if not isinstance(payload.get("observations"), list):
                raise ValueError(
                    f"FRED response for {series_id} has no observations"
                )
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
                        "realtime_start": realtime_start,
                        "realtime_end": realtime_end,
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
                "realtime_windows": len(windows),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
