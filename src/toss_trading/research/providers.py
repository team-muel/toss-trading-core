from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from zoneinfo import ZoneInfo

from toss_trading.research.data_lake import DataLake, DatasetManifest, MarketBar


TIINGO_BASE_URL = "https://api.tiingo.com/tiingo/daily"
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"
TOSS_LICENSE_TAG = "toss-openapi-internal-research-provisional"
TIINGO_LICENSE_TAG = "tiingo-internal-use-only"
SEC_LICENSE_TAG = "sec-edgar-fair-access-document-rights-provisional"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _response_body_bytes(response: Any) -> bytes:
    try:
        return response.read()
    finally:
        response.close()


def _daily_event_time(local_date: str, timezone_name: str = "America/New_York") -> str:
    session_date = date.fromisoformat(local_date)
    local_close = datetime.combine(
        session_date,
        datetime_time(hour=16),
        tzinfo=ZoneInfo(timezone_name),
    )
    return local_close.astimezone(timezone.utc).isoformat()


def _provider_timestamp(value: str) -> tuple[str, str]:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"provider timestamp has no time zone: {value!r}")
    event_time = parsed.astimezone(timezone.utc).isoformat()
    local_date = parsed.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    return event_time, local_date


def _result_page(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("Toss candle response is not a JSON object")
    result = body.get("result")
    if not isinstance(result, dict):
        raise ValueError("Toss candle response has no result object")
    return result


class TossCandleAdapter(Protocol):
    def get_candles(
        self,
        symbol: str,
        *,
        interval: str,
        count: int,
        before: str | None,
        adjusted: bool | None,
    ) -> Any: ...


def collect_toss_candle_bundle(
    adapter: TossCandleAdapter,
    *,
    symbols: Iterable[str],
    start_date: str,
    adjusted: bool,
    interval: str = "1d",
    count: int = 200,
    max_pages: int = 100,
    skip_unavailable_symbols: bool = False,
    retrieved_at: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Collect paginated, read-only Toss candles without normalizing the response."""

    if interval != "1d":
        raise ValueError("historical research collection currently supports interval=1d only")
    start = date.fromisoformat(start_date)
    canonical_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    if not canonical_symbols:
        raise ValueError("at least one Toss symbol is required")

    pages: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for symbol in canonical_symbols:
        before: str | None = None
        seen_cursors: set[str] = set()
        complete = False
        for page_number in range(1, max_pages + 1):
            try:
                result = adapter.get_candles(
                    symbol,
                    interval=interval,
                    count=count,
                    before=before,
                    adjusted=adjusted,
                )
            except Exception as exc:
                error = getattr(exc, "error", {})
                code = error.get("code") if isinstance(error, dict) else None
                if (
                    skip_unavailable_symbols
                    and getattr(exc, "status_code", None) == 404
                    and code == "stock-not-found"
                ):
                    failures.append(
                        {
                            "symbol": symbol,
                            "status_code": 404,
                            "code": code,
                            "reason": "provider_symbol_unavailable",
                        }
                    )
                    complete = True
                    break
                raise
            collected_at = retrieved_at()
            body = result.body
            result_page = _result_page(body)
            candles = result_page.get("candles")
            if not isinstance(candles, list):
                raise ValueError(f"Toss candle page for {symbol} has no candles array")
            pages.append(
                {
                    "symbol": symbol,
                    "page_number": page_number,
                    "before": before,
                    "retrieved_at": collected_at,
                    "raw_response_id": getattr(result, "raw_response_id", None),
                    "body": body,
                }
            )
            if not candles:
                complete = True
                break
            candle_dates = []
            for candle in candles:
                if isinstance(candle, dict) and isinstance(candle.get("timestamp"), str):
                    _, local_date = _provider_timestamp(candle["timestamp"])
                    candle_dates.append(date.fromisoformat(local_date))
            if candle_dates and min(candle_dates) <= start:
                complete = True
                break
            next_before = result_page.get("nextBefore")
            if next_before in (None, ""):
                complete = True
                break
            cursor = str(next_before)
            if cursor in seen_cursors:
                raise RuntimeError(f"Toss candle cursor repeated for {symbol}: {cursor}")
            seen_cursors.add(cursor)
            before = cursor
        if not complete:
            raise RuntimeError(
                f"Toss candle pagination for {symbol} exceeded max_pages={max_pages}"
            )

    return {
        "schema_version": "toss-candle-bundle-v1",
        "source": "toss-openapi",
        "created_at": retrieved_at(),
        "request": {
            "symbols": canonical_symbols,
            "start_date": start_date,
            "interval": interval,
            "count": count,
            "adjusted": adjusted,
            "max_pages": max_pages,
        },
        "pages": pages,
        "failures": failures,
    }


def ingest_toss_candle_bundle(
    lake: DataLake,
    bundle: dict[str, Any],
    *,
    code_revision: str,
    license_tag: str = TOSS_LICENSE_TAG,
    through_date: str | None = None,
) -> tuple[list[DatasetManifest], list[DatasetManifest], int]:
    if bundle.get("schema_version") != "toss-candle-bundle-v1":
        raise ValueError("unsupported Toss candle bundle schema")
    request = bundle.get("request")
    pages = bundle.get("pages")
    if not isinstance(request, dict) or not isinstance(pages, list):
        raise ValueError("invalid Toss candle bundle")
    adjusted = request.get("adjusted")
    if not isinstance(adjusted, bool):
        raise ValueError("Toss candle bundle is missing a boolean adjusted flag")
    start = date.fromisoformat(str(request["start_date"]))
    end = date.fromisoformat(through_date) if through_date else None
    interval = str(request["interval"])
    adjustment = "split_adjusted" if adjusted else "raw"
    quality_flag = "estimated" if adjusted else "ok"

    raw_manifests: list[DatasetManifest] = []
    rows_by_key: dict[tuple[str, str], MarketBar] = {}
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("body"), dict):
            raise ValueError("invalid Toss candle page")
        symbol = str(page["symbol"]).strip().upper()
        retrieved_at = str(page["retrieved_at"])
        raw = lake.store_raw(
            source="toss-openapi",
            dataset="daily-candles",
            body=page["body"],
            media_type="application/json",
            schema_version="toss-candles-response-v1",
            available_at=retrieved_at,
            request={
                "symbol": symbol,
                "interval": interval,
                "count": request.get("count"),
                "before": page.get("before"),
                "adjusted": adjusted,
            },
            license_tag=license_tag,
            code_revision=code_revision,
            retrieved_at=retrieved_at,
        )
        raw_manifests.append(raw)
        candles = _result_page(page["body"]).get("candles")
        if not isinstance(candles, list):
            raise ValueError(f"Toss candle page for {symbol} has no candles array")
        for candle in candles:
            if not isinstance(candle, dict):
                continue
            event_time, exchange_date = _provider_timestamp(str(candle["timestamp"]))
            session_date = date.fromisoformat(exchange_date)
            if session_date < start or (end is not None and session_date > end):
                continue
            row = MarketBar(
                symbol=symbol,
                event_time_utc=event_time,
                available_at=retrieved_at,
                exchange_local_date=exchange_date,
                interval=interval,
                open=str(candle["openPrice"]),
                high=str(candle["highPrice"]),
                low=str(candle["lowPrice"]),
                close=str(candle["closePrice"]),
                volume=str(candle["volume"]),
                currency=str(candle.get("currency", "USD")).upper(),
                session="regular",
                adjustment=adjustment,
                source="toss-openapi",
                source_revision=f"toss-candles-adjusted={str(adjusted).lower()}",
                raw_manifest_id=raw.manifest_id,
                quality_flag=quality_flag,
            )
            rows_by_key[(symbol, event_time)] = row

    rows = sorted(
        rows_by_key.values(),
        key=lambda row: (row.symbol, row.event_time_utc),
    )
    if not rows:
        raise ValueError("Toss candle bundle produced no rows")
    normalized = lake.write_market_bars(
        rows,
        code_revision=code_revision,
        license_tag=license_tag,
    )
    return raw_manifests, normalized, len(rows)


@dataclass
class TiingoEodClient:
    token: str
    opener: Callable[..., Any] = urllib.request.urlopen

    def fetch(self, symbol: str, *, start_date: str, end_date: str) -> bytes:
        if not self.token:
            raise RuntimeError("TIINGO_API_TOKEN is required")
        query = urllib.parse.urlencode(
            {
                "startDate": start_date,
                "endDate": end_date,
                "format": "json",
                "resampleFreq": "daily",
            }
        )
        url = f"{TIINGO_BASE_URL}/{urllib.parse.quote(symbol, safe='')}/prices?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token {self.token}",
                "Accept": "application/json",
                "User-Agent": "toss-trading-core-research/0.1",
            },
            method="GET",
        )
        return _response_body_bytes(self.opener(request, timeout=30))


def ingest_tiingo_eod_response(
    lake: DataLake,
    *,
    symbol: str,
    body: bytes,
    start_date: str,
    end_date: str,
    retrieved_at: str,
    code_revision: str,
    license_tag: str = TIINGO_LICENSE_TAG,
) -> tuple[DatasetManifest, list[DatasetManifest], int]:
    parsed = json.loads(body)
    if not isinstance(parsed, list):
        raise ValueError("Tiingo EOD response is not a JSON array")
    raw = lake.store_raw(
        source="tiingo-eod",
        dataset="daily-prices",
        body=body,
        media_type="application/json",
        schema_version="tiingo-eod-prices-v1",
        available_at=retrieved_at,
        request={
            "symbol": symbol.upper(),
            "start_date": start_date,
            "end_date": end_date,
            "resample_freq": "daily",
        },
        license_tag=license_tag,
        code_revision=code_revision,
        retrieved_at=retrieved_at,
    )
    rows: list[MarketBar] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        local_date = str(item["date"])[:10]
        event_time = _daily_event_time(local_date)
        common = {
            "symbol": symbol.upper(),
            "event_time_utc": event_time,
            "available_at": retrieved_at,
            "exchange_local_date": local_date,
            "interval": "1d",
            "currency": "USD",
            "session": "regular",
            "source": "tiingo-eod",
            "source_revision": "tiingo-eod-v1",
            "raw_manifest_id": raw.manifest_id,
            "quality_flag": "ok",
        }
        rows.append(
            MarketBar(
                **common,
                open=str(item["open"]),
                high=str(item["high"]),
                low=str(item["low"]),
                close=str(item["close"]),
                volume=str(item["volume"]),
                adjustment="raw",
            )
        )
        rows.append(
            MarketBar(
                **common,
                open=str(item["adjOpen"]),
                high=str(item["adjHigh"]),
                low=str(item["adjLow"]),
                close=str(item["adjClose"]),
                volume=str(item["adjVolume"]),
                adjustment="total_return",
            )
        )
    rows.sort(key=lambda row: (row.symbol, row.adjustment, row.event_time_utc))
    if not rows:
        raise ValueError(f"Tiingo returned no EOD rows for {symbol}")
    normalized = lake.write_market_bars(
        rows,
        code_revision=code_revision,
        license_tag=license_tag,
    )
    return raw, normalized, len(rows)


@dataclass
class SecEdgarClient:
    user_agent: str
    opener: Callable[..., Any] = urllib.request.urlopen
    minimum_interval_seconds: float = 0.12
    _last_request_at: float = 0.0

    def fetch(self, url: str) -> bytes:
        if not self.user_agent.strip():
            raise RuntimeError("SEC_USER_AGENT is required")
        delay = self.minimum_interval_seconds - (time.monotonic() - self._last_request_at)
        if delay > 0:
            time.sleep(delay)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Host": urllib.parse.urlsplit(url).hostname or "www.sec.gov",
            },
            method="GET",
        )
        body = _response_body_bytes(self.opener(request, timeout=30))
        self._last_request_at = time.monotonic()
        return body

    def ticker_map(self) -> bytes:
        return self.fetch(SEC_TICKER_MAP_URL)

    def submissions(self, cik: str) -> bytes:
        digits = "".join(character for character in cik if character.isdigit())
        if not digits:
            raise ValueError(f"invalid CIK: {cik!r}")
        url = f"{SEC_SUBMISSIONS_BASE_URL}/CIK{int(digits):010d}.json"
        return self.fetch(url)


def collect_sec_reference_data(
    lake: DataLake,
    client: SecEdgarClient,
    *,
    ciks: Iterable[str],
    code_revision: str,
    retrieved_at: Callable[[], str] = utc_now,
    license_tag: str = SEC_LICENSE_TAG,
) -> list[DatasetManifest]:
    manifests: list[DatasetManifest] = []
    ticker_retrieved = retrieved_at()
    ticker_body = client.ticker_map()
    json.loads(ticker_body)
    manifests.append(
        lake.store_raw(
            source="sec-edgar",
            dataset="company-tickers",
            body=ticker_body,
            media_type="application/json",
            schema_version="sec-company-tickers-v1",
            available_at=ticker_retrieved,
            request={"url": SEC_TICKER_MAP_URL},
            license_tag=license_tag,
            code_revision=code_revision,
            retrieved_at=ticker_retrieved,
        )
    )
    canonical_ciks = sorted(
        {f"{int(''.join(char for char in cik if char.isdigit())):010d}" for cik in ciks}
    )
    for cik in canonical_ciks:
        submission_retrieved = retrieved_at()
        body = client.submissions(cik)
        json.loads(body)
        url = f"{SEC_SUBMISSIONS_BASE_URL}/CIK{cik}.json"
        manifests.append(
            lake.store_raw(
                source="sec-edgar",
                dataset="submissions",
                body=body,
                media_type="application/json",
                schema_version="sec-submissions-v1",
                available_at=submission_retrieved,
                request={"url": url, "cik": cik},
                license_tag=license_tag,
                code_revision=code_revision,
                retrieved_at=submission_retrieved,
            )
        )
    return manifests
