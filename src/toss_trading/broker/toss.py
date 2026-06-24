from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from toss_trading.account.ledger import AccountLedger
from toss_trading.broker.credentials import TossCredentials


def _health_action(status_code: int, body: Any) -> str | None:
    if status_code < 400:
        return None
    error_text = ""
    if isinstance(body, dict):
        error_text = " ".join(
            str(body.get(key, "")) for key in ("error", "error_description", "message")
        ).lower()
    if status_code == 403 and "ip address not allowed" in error_text:
        return "register_current_ip_in_toss_openapi_allowlist"
    if status_code == 401:
        return "check_toss_credentials_or_token_scope"
    if status_code == 429:
        return "backoff_and_check_toss_rate_limit"
    return "inspect_raw_api_response"


def _redact_sensitive_response(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key in {"access_token", "refresh_token", "id_token"}:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact_sensitive_response(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_response(item) for item in value]
    return value


@dataclass(frozen=True)
class TossApiResult:
    endpoint: str
    status_code: int
    body: Any
    raw_response_id: str


class TossReadOnlyAdapter:
    """Read-only Toss Open API adapter for foundation account-state ingestion."""

    def __init__(self, credentials: TossCredentials, ledger: AccountLedger | None = None) -> None:
        self.credentials = credentials
        self.ledger = ledger
        self._access_token: str | None = None

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
        return result

    def get_accounts(self) -> TossApiResult:
        return self._get("/api/v1/accounts", account_bound=False)

    def get_holdings(self) -> TossApiResult:
        return self._get("/api/v1/holdings", account_bound=True)

    def get_orders(self, status: str | None = None) -> TossApiResult:
        endpoint = "/api/v1/orders"
        if status:
            endpoint = f"{endpoint}?{urllib.parse.urlencode({'status': status})}"
        return self._get(endpoint, account_bound=True)

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

    def _get(self, endpoint: str, *, account_bound: bool) -> TossApiResult:
        if self._access_token is None:
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
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                status_code = response.status
                response_headers = dict(response.headers.items())
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            response_headers = dict(exc.headers.items())
            raw = exc.read().decode("utf-8")
        except urllib.error.URLError as exc:
            if self.ledger is not None:
                self.ledger.record_source_health(
                    source="toss",
                    channel=health_channel,
                    source_status="error",
                    action=f"network_error:{exc.reason}",
                )
            raise RuntimeError(f"Toss API network error on {endpoint}: {exc.reason}") from exc
        body = json.loads(raw) if raw else {}
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
                request_id=response_headers.get("X-Request-Id"),
                request_payload=request_payload,
                headers=response_headers,
            )
            self.ledger.record_source_health(
                source="toss",
                channel=health_channel,
                source_status="ok" if status_code < 400 else "blocked",
                action=_health_action(status_code, body),
                last_success_at=None if status_code >= 400 else None,
            )
        if status_code >= 400:
            raise RuntimeError(f"Toss API error {status_code} on {endpoint}: {body}")
        return TossApiResult(
            endpoint=endpoint,
            status_code=status_code,
            body=body,
            raw_response_id=raw_response_id,
        )
