from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import tempfile
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


def _atomic_cache_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _cache_is_complete(
    cache_dir: Path,
    *,
    series_ids: list[str],
    observation_start: str,
    incremental_start: str,
) -> bool:
    marker = cache_dir / "complete.json"
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        through = date.fromisoformat(str(payload["realtime_end"]))
        expected_start = date.fromisoformat(str(payload["realtime_start"]))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    requested = date.fromisoformat(incremental_start)
    return (
        expected_start <= date.fromisoformat(observation_start)
        and set(payload.get("series", [])) == set(series_ids)
        and requested <= through + timedelta(days=1)
        and all(
            (cache_dir / f"series={item}").is_dir()
            and any((cache_dir / f"series={item}").glob("history-*.json"))
            for item in series_ids
        )
    )


def _cache_envelopes(cache_dir: Path, series_id: str) -> list[dict[str, Any]]:
    directory = cache_dir / f"series={series_id}"
    envelopes = []
    for path in sorted(directory.glob("history-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
            raise ValueError(f"invalid FRED vintage cache envelope: {path}")
        envelopes.append(payload)
    return envelopes


def _write_history_cache(
    cache_dir: Path,
    series_id: str,
    envelopes: list[dict[str, Any]],
) -> None:
    directory = cache_dir / f"series={series_id}"
    directory.mkdir(parents=True, exist_ok=True)
    expected: set[Path] = set()
    for index, envelope in enumerate(envelopes):
        destination = directory / f"history-{index:03d}.json"
        _atomic_cache_json(destination, envelope)
        expected.add(destination)
    for path in directory.glob("history-*.json"):
        if path not in expected:
            path.unlink()


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
    parser.add_argument(
        "--cache-dir",
        help=(
            "Persistent full-vintage cache. Missing or discontinuous caches are "
            "bootstrapped from observation-start; daily runs then fetch revisions only."
        ),
    )
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
    series_ids = [item["series_id"].strip().upper() for item in series]
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    full_refresh = (
        cache_dir is not None
        and (
            args.realtime_start <= args.observation_start
            or not _cache_is_complete(
                cache_dir,
                series_ids=series_ids,
                observation_start=args.observation_start,
                incremental_start=args.realtime_start,
            )
        )
    )
    effective_start = args.observation_start if full_refresh else args.realtime_start
    windows = _realtime_windows(effective_start, args.realtime_end)
    fetched_by_series: dict[str, list[dict[str, Any]]] = {}
    for item in series:
        series_id = item["series_id"].strip().upper()
        fetched: list[dict[str, Any]] = []
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
            request_metadata = {
                "endpoint": FRED_OBSERVATIONS_URL,
                "series_id": series_id,
                "output_type": 3,
                "realtime_start": realtime_start,
                "realtime_end": realtime_end,
                "observation_start": args.observation_start,
            }
            fetched.append(
                {
                    "retrieved_at": retrieved,
                    "request": request_metadata,
                    "response": payload,
                }
            )
        fetched_by_series[series_id] = fetched

    if cache_dir is not None and full_refresh:
        for series_id, envelopes in fetched_by_series.items():
            _write_history_cache(cache_dir, series_id, envelopes)
        _atomic_cache_json(
            cache_dir / "complete.json",
            {
                "schema_version": "fred-alfred-vintage-cache-v1",
                "realtime_start": args.observation_start,
                "realtime_end": args.realtime_end,
                "series": series_ids,
                "updated_at": utc_now(),
            },
        )

    for series_id in series_ids:
        materialized = fetched_by_series[series_id]
        if cache_dir is not None and not full_refresh:
            materialized = _cache_envelopes(cache_dir, series_id) + materialized
        for envelope in materialized:
            request_metadata = envelope["request"]
            retrieved = str(envelope["retrieved_at"])
            manifests.append(
                lake.store_raw(
                    source="fred-alfred",
                    dataset="series-observation-revisions",
                    body=envelope["response"],
                    media_type="application/json",
                    schema_version="fred-observations-output-type-3-v1",
                    available_at=retrieved,
                    request=request_metadata,
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
                "fetched_realtime_windows": len(windows),
                "full_history_cache_refreshed": full_refresh,
                "history_complete": cache_dir is None or bool(
                    _cache_is_complete(
                        cache_dir,
                        series_ids=series_ids,
                        observation_start=args.observation_start,
                        incremental_start=args.realtime_start,
                    )
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
