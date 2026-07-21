"""Strict normalizers for the approved Toss OpenAPI response envelopes.

Do not use heuristic "first list found" parsing at the broker boundary.  A
valid empty response and an incompatible 200 response must have different
outcomes, otherwise an API change can silently become an empty account.
"""

from __future__ import annotations

from typing import Any


class TossContractError(ValueError):
    pass


def _result(body: Any, endpoint: str) -> Any:
    if not isinstance(body, dict) or "result" not in body:
        raise TossContractError(f"{endpoint}: expected JSON object with result")
    return body["result"]


def _object(value: Any, endpoint: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TossContractError(f"{endpoint}: expected result object")
    return value


def _array(value: Any, endpoint: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TossContractError(f"{endpoint}: expected result array of objects")
    return value


def require_accounts(body: Any) -> list[dict[str, Any]]:
    accounts = _array(_result(body, "/api/v1/accounts"), "/api/v1/accounts")
    for account in accounts:
        if not account.get("accountSeq"):
            raise TossContractError("/api/v1/accounts: accountSeq is required")
    return accounts


def holdings_items(body: Any) -> list[dict[str, Any]]:
    result = _object(_result(body, "/api/v1/holdings"), "/api/v1/holdings")
    items = _array(result.get("items"), "/api/v1/holdings.result.items")
    for item in items:
        required = (
            "symbol",
            "currency",
            "quantity",
            "lastPrice",
            "averagePurchasePrice",
            "marketValue",
            "profitLoss",
            "cost",
        )
        if any(key not in item for key in required):
            raise TossContractError("/api/v1/holdings: incomplete holding item")
        if not isinstance(item["marketValue"], dict):
            raise TossContractError("/api/v1/holdings: marketValue must be an object")
        if not isinstance(item["profitLoss"], dict):
            raise TossContractError("/api/v1/holdings: profitLoss must be an object")
        if not isinstance(item["cost"], dict):
            raise TossContractError("/api/v1/holdings: cost must be an object")
    return items


def orders_page(body: Any, *, status: str) -> tuple[list[dict[str, Any]], str | None, bool]:
    result = _object(_result(body, "/api/v1/orders"), "/api/v1/orders")
    orders = _array(result.get("orders"), "/api/v1/orders.result.orders")
    has_next = result.get("hasNext")
    next_cursor = result.get("nextCursor")
    if not isinstance(has_next, bool):
        raise TossContractError("/api/v1/orders: hasNext must be boolean")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise TossContractError("/api/v1/orders: nextCursor must be string or null")
    if status == "OPEN" and has_next:
        raise TossContractError("/api/v1/orders: OPEN order listing must not paginate")
    return orders, next_cursor, has_next


def order_detail(body: Any) -> dict[str, Any]:
    result = _object(_result(body, "/api/v1/orders/{orderId}"), "/api/v1/orders/{orderId}")
    if not result.get("orderId") or not isinstance(result.get("execution"), dict):
        raise TossContractError("/api/v1/orders/{orderId}: incomplete order detail")
    return result


def require_buying_power(body: Any) -> dict[str, Any]:
    result = _object(_result(body, "/api/v1/buying-power"), "/api/v1/buying-power")
    if not result.get("currency") or result.get("cashBuyingPower") in (None, ""):
        raise TossContractError("/api/v1/buying-power: currency and cashBuyingPower are required")
    return result


def require_sellable_quantity(body: Any) -> dict[str, Any]:
    result = _object(_result(body, "/api/v1/sellable-quantity"), "/api/v1/sellable-quantity")
    if result.get("sellableQuantity") in (None, ""):
        raise TossContractError("/api/v1/sellable-quantity: sellableQuantity is required")
    return result


def commission_rate_items(body: Any) -> list[dict[str, Any]]:
    items = _array(_result(body, "/api/v1/commissions"), "/api/v1/commissions")
    for item in items:
        if not item.get("marketCountry") or item.get("commissionRate") in (None, ""):
            raise TossContractError("/api/v1/commissions: marketCountry and commissionRate are required")
    return items
