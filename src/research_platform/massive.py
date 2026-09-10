from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


MASSIVE_BASE_URL = "https://api.massive.com"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "toss-research/1"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Massive response is not a JSON object")
    if payload.get("status") not in {None, "OK", "DELAYED"}:
        raise RuntimeError(f"Massive request failed: {payload.get('status')}")
    return payload


def _with_api_key(url: str, api_key: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key.lower() != "apikey"]
    query.append(("apiKey", api_key))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), "")
    )


def collect_common_stock_reference(
    api_key: str,
    *,
    fetch_json: Callable[[str], dict[str, Any]] = _request_json,
) -> dict[str, Any]:
    if not api_key.strip():
        raise ValueError("Massive API key is required")
    url = (
        f"{MASSIVE_BASE_URL}/v3/reference/tickers"
        "?market=stocks&active=true&type=CS&limit=1000&sort=ticker"
    )
    rows: dict[str, dict[str, Any]] = {}
    pages = 0
    while url:
        payload = fetch_json(_with_api_key(url, api_key))
        pages += 1
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("ticker") or "").strip().upper()
            if not symbol:
                continue
            rows[symbol] = {
                "symbol": symbol,
                "name": item.get("name"),
                "primary_exchange": item.get("primary_exchange"),
                "currency_name": item.get("currency_name"),
                "cik": item.get("cik"),
                "composite_figi": item.get("composite_figi"),
                "active": item.get("active") is True,
                "type": item.get("type"),
            }
        next_url = payload.get("next_url")
        url = str(next_url) if next_url else ""
        if pages > 100:
            raise RuntimeError("Massive reference pagination exceeded 100 pages")
    return {
        "schema_version": "massive-common-stock-reference-v1",
        "retrieved_at": _utc_now(),
        "source": "massive",
        "page_count": pages,
        "symbol_count": len(rows),
        "symbols": [rows[symbol] for symbol in sorted(rows)],
    }


def normalize_grouped_daily(
    payload: dict[str, Any],
    *,
    market_date: str,
    allowed_symbols: set[str],
    retrieved_at: str,
) -> list[dict[str, Any]]:
    date.fromisoformat(market_date)
    rows = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("T") or item.get("ticker") or "").strip().upper()
        if symbol not in allowed_symbols:
            continue
        values = {
            "open": item.get("o"),
            "high": item.get("h"),
            "low": item.get("l"),
            "close": item.get("c"),
            "volume": item.get("v"),
        }
        if any(value is None for value in values.values()):
            continue
        rows.append(
            {
                "symbol": symbol,
                "exchange_local_date": market_date,
                **values,
                "currency": "USD",
                "adjustment": "split_adjusted",
                "source": "massive-grouped-daily",
                "available_at": retrieved_at,
            }
        )
    return rows


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def collect_grouped_daily_history(
    api_key: str,
    *,
    start_date: str,
    through_date: str,
    allowed_symbols: set[str],
    raw_directory: str | Path,
    request_interval_seconds: float = 12.5,
    fetch_json: Callable[[str], dict[str, Any]] = _request_json,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not api_key.strip():
        raise ValueError("Massive API key is required")
    start = date.fromisoformat(start_date)
    through = date.fromisoformat(through_date)
    if start > through:
        raise ValueError("Massive grouped start date is after through date")
    if not allowed_symbols:
        raise ValueError("Massive grouped collection requires common stocks")
    raw_root = Path(raw_directory)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    requested = 0
    reused = 0
    current = start
    while current <= through:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        market_date = current.isoformat()
        raw_path = raw_root / f"{market_date}.json"
        if raw_path.exists():
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            retrieved_at = str(payload.get("_retrieved_at") or "")
            reused += 1
        else:
            if requested:
                sleep(request_interval_seconds)
            url = (
                f"{MASSIVE_BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/"
                f"{market_date}?adjusted=true&include_otc=false"
            )
            payload = fetch_json(_with_api_key(url, api_key))
            retrieved_at = _utc_now()
            payload = {**payload, "_retrieved_at": retrieved_at}
            _atomic_bytes(
                raw_path,
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
            )
            requested += 1
        for row in normalize_grouped_daily(
            payload,
            market_date=market_date,
            allowed_symbols=allowed_symbols,
            retrieved_at=retrieved_at,
        ):
            rows[(row["symbol"], market_date)] = row
        current += timedelta(days=1)
    normalized = [rows[key] for key in sorted(rows)]
    return normalized, {
        "schema_version": "massive-grouped-collection-summary-v1",
        "start_date": start_date,
        "through_date": through_date,
        "allowed_symbol_count": len(allowed_symbols),
        "normalized_row_count": len(normalized),
        "requested_date_count": requested,
        "reused_date_count": reused,
        "adjusted": True,
        "include_otc": False,
    }
