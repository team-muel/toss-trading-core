from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import time
from dataclasses import dataclass, replace
from typing import Any

from toss_trading.account.ledger import AccountLedger
from toss_trading.broker.base import BrokerCapabilities
from toss_trading.broker.credentials import TossCredentials
from toss_trading.runtime import TokenBucket


SECRET_RESPONSE_KEYS = {"access_token", "refresh_token", "id_token"}
ACCOUNT_NUMBER_KEYS = {"accountno", "accountnumber", "account_no"}
ACCOUNT_IDENTIFIER_KEYS = {
    "accountseq",
    "account_seq",
    "accountid",
    "account_id",
    "accountidentifier",
    "account_identifier",
}


def _extract_toss_error(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    error = body.get("error")
    if isinstance(error, dict):
        return {
            "code": error.get("code") or body.get("code"),
            "message": error.get("message") or body.get("message"),
            "request_id": error.get("requestId") or error.get("request_id") or body.get("requestId"),
            "field": error.get("data", {}).get("field") if isinstance(error.get("data"), dict) else None,
            "description": error.get("error_description") or body.get("error_description"),
        }
    return {
        "code": body.get("error") or body.get("code"),
        "message": body.get("message"),
        "request_id": body.get("requestId") or body.get("request_id"),
        "field": body.get("field"),
        "description": body.get("error_description"),
    }


def _header(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _health_action(status_code: int, body: Any) -> str | None:
    if status_code < 400:
        return None
    parsed = _extract_toss_error(body)
    error_text = " ".join(
        str(parsed.get(key, "")) for key in ("code", "message", "description", "field")
    ).lower()
    if status_code == 403 and "ip address not allowed" in error_text:
        return "register_current_ip_in_toss_openapi_allowlist"
    if status_code == 401:
        return "check_toss_credentials_or_token_scope"
    if status_code == 429:
        return "backoff_and_check_toss_rate_limit"
    return "inspect_raw_api_response"


class TossApiError(RuntimeError):
    def __init__(self, *, endpoint: str, status_code: int, body: Any) -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        self.error = _extract_toss_error(body)
        detail = ", ".join(
            f"{key}={value}"
            for key, value in self.error.items()
            if value not in (None, "")
        )
        suffix = f": {detail}" if detail else ""
        super().__init__(f"Toss API error {status_code} on {endpoint}{suffix}")


def _redact_sensitive_response(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized_key = key.lower()
            if normalized_key in SECRET_RESPONSE_KEYS:
                redacted[key] = "***REDACTED***"
            elif normalized_key in ACCOUNT_NUMBER_KEYS:
                redacted[key] = _redact_account_number(item)
            elif normalized_key in ACCOUNT_IDENTIFIER_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact_sensitive_response(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_response(item) for item in value]
    return value


def _redact_account_number(value: Any) -> str:
    text = str(value)
    if len(text) <= 4:
        return "****"
    return f"{'*' * (len(text) - 4)}{text[-4:]}"


def _decode_response(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _parse_response_body(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"non_json_response": raw[:500]}


def _response_request_id(headers: dict[str, str], body: Any) -> str | None:
    return _header(headers, "X-Request-Id") or _extract_toss_error(body).get("request_id")


@dataclass(frozen=True)
class TossApiResult:
    endpoint: str
    status_code: int
    body: Any
    raw_response_id: str


class TossReadOnlyAdapter:
    """Read-only Toss Open API adapter for foundation account-state ingestion."""

    capabilities = BrokerCapabilities(
        market_data=True,
        order_entry=False,
        order_cancel=False,
        fills=True,
        balances=True,
        conditional_order_entry=False,
        conditional_order_modify=False,
        options_trading=False,
        margin_status=False,
    )

    def __init__(
        self,
        credentials: TossCredentials,
        ledger: AccountLedger | None = None,
        rate_limiter: TokenBucket | None = None,
    ) -> None:
        self.credentials = credentials
        self.ledger = ledger
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0
        self.run_id: str | None = None
        default_limiter = rate_limiter or TokenBucket(
            capacity=float(os.environ.get("TOSS_RATE_LIMIT_CAPACITY", "20")),
            refill_per_second=float(os.environ.get("TOSS_RATE_LIMIT_REFILL_PER_SECOND", "5")),
        )
        self.rate_limiter = default_limiter
        self.rate_limiters: dict[str, TokenBucket] = {"DEFAULT": default_limiter}

    def with_account(self, account_seq: str) -> "TossReadOnlyAdapter":
        clone = TossReadOnlyAdapter(
            replace(self.credentials, account_seq=account_seq),
            ledger=self.ledger,
            rate_limiter=self.rate_limiter,
        )
        clone._access_token = self._access_token
        clone._access_token_expires_at = self._access_token_expires_at
        clone.rate_limiters = self.rate_limiters
        clone.run_id = self.run_id
        return clone

    def _api_group(self, endpoint: str) -> str:
        path = endpoint.split("?", 1)[0]
        if path == "/oauth2/token":
            return "AUTH"
        if path == "/api/v1/accounts":
            return "ACCOUNT"
        if path == "/api/v1/holdings":
            return "ASSET"
        if path == "/api/v1/orders" or path.startswith("/api/v1/orders/"):
            return "ORDER_HISTORY"
        if path in {
            "/api/v1/buying-power",
            "/api/v1/sellable-quantity",
            "/api/v1/commissions",
        }:
            return "ORDER_INFO"
        if path == "/api/v1/candles":
            return "MARKET_DATA_CHART"
        if path in {
            "/api/v1/prices",
            "/api/v1/orderbook",
            "/api/v1/trades",
            "/api/v1/price-limits",
        }:
            return "MARKET_DATA"
        if path == "/api/v1/stocks/all":
            return "STOCK_ALL"
        if path.startswith("/api/v1/stocks/") and path.rsplit("/", 1)[-1] in {
            "investor-trading",
            "program-trades",
            "short-selling",
            "credit-trades",
            "securities-lending",
        }:
            return "STOCK_TRADING_TREND"
        if path == "/api/v1/stocks" or (
            path.startswith("/api/v1/stocks/") and path.endswith("/warnings")
        ):
            return "STOCK"
        if path in {
            "/api/v1/exchange-rate",
            "/api/v1/market-calendar/KR",
            "/api/v1/market-calendar/US",
        }:
            return "MARKET_INFO"
        if path == "/api/v1/rankings":
            return "RANKING"
        if path == "/api/v1/market-indicators/prices":
            return "MARKET_INDICATOR_PRICE"
        if path.startswith("/api/v1/market-indicators/") and path.endswith(
            "/investor-trading"
        ):
            return "MARKET_INDICATOR"
        if (
            path.startswith("/api/v1/market-indicators/")
            and path.endswith("/candles")
        ):
            return "MARKET_INDICATOR_CHART"
        return "DEFAULT"

    def _limiter_for(self, endpoint: str) -> TokenBucket:
        group = self._api_group(endpoint)
        if group not in self.rate_limiters:
            self.rate_limiters[group] = TokenBucket(
                capacity=float(os.environ.get("TOSS_RATE_LIMIT_CAPACITY", "5")),
                refill_per_second=float(os.environ.get("TOSS_RATE_LIMIT_REFILL_PER_SECOND", "5")),
            )
        return self.rate_limiters[group]

    def refresh_token(self) -> TossApiResult:
        endpoint = "/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.credentials.client_id,
            "client_secret": self.credentials.client_secret,
        }
        body = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            self.credentials.base_url.rstrip("/") + endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        result = self._send(request, endpoint, request_payload={"grant_type": "client_credentials"})
        token = result.body.get("access_token") if isinstance(result.body, dict) else None
        if not token:
            raise RuntimeError("Toss token response did not include access_token")
        self._access_token = str(token)
        expires_in = result.body.get("expires_in") if isinstance(result.body, dict) else None
        try:
            self._access_token_expires_at = time.monotonic() + max(0, int(expires_in) - 30)
        except (TypeError, ValueError):
            self._access_token_expires_at = time.monotonic()
        return result

    def get_accounts(self) -> TossApiResult:
        return self._get("/api/v1/accounts", account_bound=False)

    def get_holdings(self) -> TossApiResult:
        return self._get("/api/v1/holdings", account_bound=True)

    def get_orders(
        self,
        status: str | None = None,
        *,
        symbol: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        cursor: str | None = None,
        limit: int | None = 100,
    ) -> TossApiResult:
        endpoint = "/api/v1/orders"
        query: dict[str, str | int] = {}
        if status:
            query["status"] = status
        if symbol:
            query["symbol"] = symbol
        if from_date:
            query["from"] = from_date
        if to_date:
            query["to"] = to_date
        if cursor:
            query["cursor"] = cursor
        if limit:
            query["limit"] = limit
        if query:
            endpoint = f"{endpoint}?{urllib.parse.urlencode(query)}"
        return self._get(endpoint, account_bound=True)

    def get_all_orders(
        self,
        status: str,
        *,
        symbol: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 100,
        max_pages: int = 20,
    ) -> list[TossApiResult]:
        if status not in {"OPEN", "CLOSED"}:
            raise ValueError("Toss order status group must be OPEN or CLOSED")
        results: list[TossApiResult] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for page_number in range(max_pages):
            result = self.get_orders(
                status=status,
                symbol=symbol,
                from_date=from_date,
                to_date=to_date,
                cursor=cursor,
                limit=limit,
            )
            results.append(result)
            page = result.body.get("result") if isinstance(result.body, dict) else {}
            if not isinstance(page, dict) or not page.get("hasNext"):
                break
            cursor = page.get("nextCursor")
            if not cursor or cursor in seen_cursors:
                raise RuntimeError("Toss order pagination is incomplete or cursor repeated")
            seen_cursors.add(cursor)
        else:
            raise RuntimeError(
                f"Toss order pagination exceeded max_pages={max_pages}; snapshot is incomplete"
            )
        return results

    def get_order(self, order_id: str) -> TossApiResult:
        encoded = urllib.parse.quote(order_id, safe="")
        return self._get(f"/api/v1/orders/{encoded}", account_bound=True)

    def get_buying_power(self, **query: str) -> TossApiResult:
        endpoint = "/api/v1/buying-power"
        if query:
            endpoint = f"{endpoint}?{urllib.parse.urlencode(query)}"
        return self._get(endpoint, account_bound=True)

    def get_sellable_quantity(self, **query: str) -> TossApiResult:
        endpoint = "/api/v1/sellable-quantity"
        if query:
            endpoint = f"{endpoint}?{urllib.parse.urlencode(query)}"
        return self._get(endpoint, account_bound=True)

    def get_commissions(self, **query: str) -> TossApiResult:
        endpoint = "/api/v1/commissions"
        if query:
            endpoint = f"{endpoint}?{urllib.parse.urlencode(query)}"
        return self._get(endpoint, account_bound=True)

    # ------------------------------------------------------------------ #
    # Read-only Toss market data (separate from account state)
    # ------------------------------------------------------------------ #

    def get_prices(self, symbols: list[str]) -> TossApiResult:
        """Current prices for up to 200 symbols (`GET /api/v1/prices`)."""
        if not symbols:
            raise ValueError("get_prices requires at least one symbol")
        if len(symbols) > 200:
            raise ValueError("Toss /prices accepts at most 200 symbols per call")
        query = urllib.parse.urlencode({"symbols": ",".join(symbols)})
        return self._get(f"/api/v1/prices?{query}", account_bound=False)

    def get_orderbook(self, symbol: str) -> TossApiResult:
        """Current bid/ask levels for one KR or US stock."""
        if not symbol:
            raise ValueError("get_orderbook requires a symbol")
        query = urllib.parse.urlencode({"symbol": symbol})
        return self._get(f"/api/v1/orderbook?{query}", account_bound=False)

    def get_trades(self, symbol: str, *, count: int = 50) -> TossApiResult:
        """Most recent intraday trades for one KR or US stock."""
        if not symbol:
            raise ValueError("get_trades requires a symbol")
        if not 1 <= count <= 50:
            raise ValueError("trade count must be between 1 and 50")
        query = urllib.parse.urlencode({"symbol": symbol, "count": count})
        return self._get(f"/api/v1/trades?{query}", account_bound=False)

    def get_price_limits(self, symbol: str) -> TossApiResult:
        """Current upper and lower price limits for one KR or US stock."""
        if not symbol:
            raise ValueError("get_price_limits requires a symbol")
        query = urllib.parse.urlencode({"symbol": symbol})
        return self._get(f"/api/v1/price-limits?{query}", account_bound=False)

    def get_stocks(self, symbols: list[str]) -> TossApiResult:
        """Reference data for up to 200 symbols (`GET /api/v1/stocks`)."""
        if not symbols:
            raise ValueError("get_stocks requires at least one symbol")
        if len(symbols) > 200:
            raise ValueError("Toss /stocks accepts at most 200 symbols per call")
        query = urllib.parse.urlencode({"symbols": ",".join(symbols)})
        return self._get(f"/api/v1/stocks?{query}", account_bound=False)

    def get_all_stocks(
        self,
        market: str,
        *,
        status: str = "ACTIVE",
        security_type: str | None = None,
        common_share: bool | None = None,
    ) -> TossApiResult:
        """All Toss-tradable instruments for one exchange/market."""
        normalized_market = market.upper()
        allowed_markets = {
            "KOSPI",
            "KOSDAQ",
            "NYSE",
            "NASDAQ",
            "AMEX",
            "KR_ETC",
            "US_ETC",
        }
        allowed_statuses = {"SCHEDULED", "ACTIVE", "DELISTED"}
        allowed_security_types = {
            "STOCK",
            "FOREIGN_STOCK",
            "DEPOSITARY_RECEIPT",
            "INFRASTRUCTURE_FUND",
            "REIT",
            "ETF",
            "FOREIGN_ETF",
            "ETN",
            "STOCK_WARRANTS",
        }
        normalized_status = status.upper()
        if normalized_market not in allowed_markets:
            raise ValueError(f"unsupported Toss stock market: {market}")
        if normalized_status not in allowed_statuses:
            raise ValueError(f"unsupported Toss listing status: {status}")
        query: dict[str, str] = {
            "market": normalized_market,
            "status": normalized_status,
        }
        if security_type is not None:
            normalized_type = security_type.upper()
            if normalized_type not in allowed_security_types:
                raise ValueError(f"unsupported Toss security type: {security_type}")
            query["securityType"] = normalized_type
        if common_share is not None:
            if not isinstance(common_share, bool):
                raise ValueError("common_share must be boolean or None")
            query["commonShare"] = "true" if common_share else "false"
        return self._get(
            f"/api/v1/stocks/all?{urllib.parse.urlencode(query)}",
            account_bound=False,
        )

    def get_stock_warnings(self, symbol: str) -> TossApiResult:
        encoded = urllib.parse.quote(symbol, safe="")
        return self._get(
            f"/api/v1/stocks/{encoded}/warnings",
            account_bound=False,
        )

    def _get_stock_trading_trend(
        self,
        symbol: str,
        dataset: str,
        *,
        count: int,
        until: str | None,
    ) -> TossApiResult:
        if not symbol:
            raise ValueError("stock trading-trend request requires a symbol")
        if not 1 <= count <= 100:
            raise ValueError("stock trading-trend count must be between 1 and 100")
        encoded = urllib.parse.quote(symbol, safe="")
        query: dict[str, str | int] = {"count": count}
        if until:
            query["until"] = until
        return self._get(
            f"/api/v1/stocks/{encoded}/{dataset}?{urllib.parse.urlencode(query)}",
            account_bound=False,
        )

    def get_stock_investor_trading(
        self,
        symbol: str,
        *,
        count: int = 100,
        until: str | None = None,
    ) -> TossApiResult:
        return self._get_stock_trading_trend(
            symbol, "investor-trading", count=count, until=until
        )

    def get_stock_program_trades(
        self,
        symbol: str,
        *,
        count: int = 100,
        until: str | None = None,
    ) -> TossApiResult:
        return self._get_stock_trading_trend(
            symbol, "program-trades", count=count, until=until
        )

    def get_stock_short_selling(
        self,
        symbol: str,
        *,
        count: int = 100,
        until: str | None = None,
    ) -> TossApiResult:
        return self._get_stock_trading_trend(
            symbol, "short-selling", count=count, until=until
        )

    def get_stock_credit_trades(
        self,
        symbol: str,
        *,
        count: int = 100,
        until: str | None = None,
    ) -> TossApiResult:
        return self._get_stock_trading_trend(
            symbol, "credit-trades", count=count, until=until
        )

    def get_stock_securities_lending(
        self,
        symbol: str,
        *,
        count: int = 100,
        until: str | None = None,
    ) -> TossApiResult:
        return self._get_stock_trading_trend(
            symbol, "securities-lending", count=count, until=until
        )

    def get_exchange_rate(
        self,
        *,
        base_currency: str,
        quote_currency: str,
        date_time: str | None = None,
    ) -> TossApiResult:
        query = {
            "baseCurrency": base_currency,
            "quoteCurrency": quote_currency,
        }
        if date_time:
            query["dateTime"] = date_time
        return self._get(
            f"/api/v1/exchange-rate?{urllib.parse.urlencode(query)}",
            account_bound=False,
        )

    def get_market_calendar(
        self,
        market_country: str,
        *,
        calendar_date: str | None = None,
    ) -> TossApiResult:
        market = market_country.upper()
        if market not in {"KR", "US"}:
            raise ValueError("market_country must be KR or US")
        endpoint = f"/api/v1/market-calendar/{market}"
        if calendar_date:
            endpoint = f"{endpoint}?{urllib.parse.urlencode({'date': calendar_date})}"
        return self._get(endpoint, account_bound=False)

    def get_rankings(
        self,
        *,
        ranking_type: str,
        market_country: str,
        duration: str,
        exclude_investment_caution: bool = True,
        count: int = 100,
    ) -> TossApiResult:
        allowed_types = {
            "MARKET_TRADING_AMOUNT",
            "MARKET_TRADING_VOLUME",
            "TOP_GAINERS",
            "TOP_LOSERS",
            "TOSS_SECURITIES_TRADING_AMOUNT",
            "TOSS_SECURITIES_TRADING_VOLUME",
        }
        if ranking_type not in allowed_types:
            raise ValueError(f"unsupported Toss ranking type: {ranking_type}")
        if market_country not in {"KR", "US"}:
            raise ValueError("market_country must be KR or US")
        if duration not in {"realtime", "1d", "1w", "1mo", "3mo", "6mo", "1y"}:
            raise ValueError(f"unsupported Toss ranking duration: {duration}")
        if ranking_type in {"TOP_GAINERS", "TOP_LOSERS"} and duration == "realtime":
            raise ValueError("TOP_GAINERS and TOP_LOSERS do not support realtime")
        if not 1 <= count <= 100:
            raise ValueError("ranking count must be between 1 and 100")
        query = urllib.parse.urlencode(
            {
                "type": ranking_type,
                "marketCountry": market_country,
                "duration": duration,
                "excludeInvestmentCaution": (
                    "true" if exclude_investment_caution else "false"
                ),
                "count": count,
            }
        )
        return self._get(f"/api/v1/rankings?{query}", account_bound=False)

    def get_market_indicator_prices(self, symbols: list[str]) -> TossApiResult:
        if not symbols:
            raise ValueError("get_market_indicator_prices requires symbols")
        if len(symbols) > 200:
            raise ValueError(
                "Toss /market-indicators/prices accepts at most 200 symbols"
            )
        query = urllib.parse.urlencode({"symbols": ",".join(symbols)})
        return self._get(
            f"/api/v1/market-indicators/prices?{query}",
            account_bound=False,
        )

    def get_market_indicator_candles(
        self,
        symbol: str,
        *,
        interval: str = "1d",
        count: int = 100,
        before: str | None = None,
    ) -> TossApiResult:
        if interval not in {"1m", "1d"}:
            raise ValueError("interval must be '1m' or '1d'")
        if symbol.startswith("KR_BOND_") and interval != "1d":
            raise ValueError("Korean bond indicators support daily candles only")
        if not 1 <= count <= 200:
            raise ValueError("count must be between 1 and 200")
        encoded = urllib.parse.quote(symbol, safe="")
        query: dict[str, str | int] = {"interval": interval, "count": count}
        if before:
            query["before"] = before
        return self._get(
            f"/api/v1/market-indicators/{encoded}/candles?"
            f"{urllib.parse.urlencode(query)}",
            account_bound=False,
        )

    def get_market_indicator_investor_trading(
        self,
        symbol: str,
        *,
        interval: str = "1d",
        count: int = 100,
        until: str | None = None,
    ) -> TossApiResult:
        if symbol not in {"KOSPI", "KOSDAQ"}:
            raise ValueError("investor trading supports KOSPI or KOSDAQ only")
        if interval not in {"1d", "1w", "1mo", "1y"}:
            raise ValueError(f"unsupported investor-trading interval: {interval}")
        if not 1 <= count <= 100:
            raise ValueError("count must be between 1 and 100")
        query: dict[str, str | int] = {"interval": interval, "count": count}
        if until:
            query["until"] = until
        return self._get(
            f"/api/v1/market-indicators/{symbol}/investor-trading?"
            f"{urllib.parse.urlencode(query)}",
            account_bound=False,
        )

    def get_candles(
        self,
        symbol: str,
        *,
        interval: str = "1d",
        count: int = 100,
        before: str | None = None,
        adjusted: bool | None = None,
    ) -> TossApiResult:
        """Candle chart for one symbol (`GET /api/v1/candles`).

        Market data is not account-bound.  ``interval`` is ``1m`` or ``1d`` and
        ``count`` is capped at 200 by Toss.
        """
        if interval not in {"1m", "1d"}:
            raise ValueError("interval must be '1m' or '1d'")
        if not 1 <= count <= 200:
            raise ValueError("count must be between 1 and 200")
        query: dict[str, str | int] = {"symbol": symbol, "interval": interval, "count": count}
        if before:
            query["before"] = before
        if adjusted is not None:
            query["adjusted"] = "true" if adjusted else "false"
        return self._get(f"/api/v1/candles?{urllib.parse.urlencode(query)}", account_bound=False)

    def _get(self, endpoint: str, *, account_bound: bool) -> TossApiResult:
        if self._access_token is None or time.monotonic() >= self._access_token_expires_at:
            self.refresh_token()
        headers = {"Authorization": f"Bearer {self._access_token}"}
        if account_bound:
            if not self.credentials.account_seq:
                raise RuntimeError("account-bound Toss API call requires account_seq")
            headers["X-Tossinvest-Account"] = self.credentials.account_seq
        request = urllib.request.Request(
            self.credentials.base_url.rstrip("/") + endpoint,
            headers=headers,
            method="GET",
        )
        return self._send(request, endpoint)

    def _send(
        self,
        request: urllib.request.Request,
        endpoint: str,
        request_payload: Any | None = None,
    ) -> TossApiResult:
        health_channel = f"rest:{endpoint.split('?', 1)[0]}"
        status_code = 0
        response_headers: dict[str, str] = {}
        limiter = self._limiter_for(endpoint)
        retry_count = 0
        while True:
            limiter.acquire()
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    status_code = response.status
                    response_headers = dict(response.headers.items())
                    raw = _decode_response(response.read())
            except urllib.error.HTTPError as exc:
                status_code = exc.code
                response_headers = dict(exc.headers.items())
                raw = _decode_response(exc.read())
            except urllib.error.URLError as exc:
                if self.ledger is not None:
                    self.ledger.record_source_health(
                        source="toss",
                        channel=health_channel,
                        source_status="error",
                        action=f"network_error:{exc.reason}",
                        run_id=self.run_id,
                    )
                raise RuntimeError(f"Toss API network error on {endpoint}: {exc.reason}") from exc
            limiter.update_from_headers(response_headers)
            if status_code == 429 and request.get_method() == "GET" and retry_count < 2:
                retry_count += 1
                wait = TokenBucket.retry_after_seconds(response_headers)
                time.sleep(wait if wait is not None else min(4.0, float(2**retry_count)))
                continue
            break
        body = _parse_response_body(raw)
        request_headers = {key.lower(): value for key, value in request.header_items()}
        account_seq = request_headers.get("x-tossinvest-account")
        raw_response_id = ""
        if self.ledger is not None:
            stored_body = _redact_sensitive_response(body)
            raw_response_id = self.ledger.save_raw_api_response(
                source="toss",
                source_type="broker",
                endpoint=endpoint,
                http_method=request.get_method(),
                body=stored_body,
                account_seq=account_seq,
                status_code=status_code,
                request_id=_response_request_id(response_headers, body),
                request_payload=request_payload,
                headers=response_headers,
                channel=health_channel,
                run_id=self.run_id,
            )
            self.ledger.record_source_health(
                source="toss",
                channel=health_channel,
                source_status="ok" if status_code < 400 else "blocked",
                action=_health_action(status_code, body),
                last_success_at=None if status_code >= 400 else None,
                run_id=self.run_id,
            )
        if status_code >= 400:
            raise TossApiError(endpoint=endpoint, status_code=status_code, body=body)
        return TossApiResult(
            endpoint=endpoint,
            status_code=status_code,
            body=body,
            raw_response_id=raw_response_id,
        )
