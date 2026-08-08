from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from toss_trading.engines import Signal


def decimal_text(value: Any, *, field: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return format(number, "f")


@dataclass(frozen=True)
class OrderIntent:
    """Exact order payload and evidence context approved by the risk layer."""

    account_seq: str
    snapshot_run_id: str
    policy_hash: str
    client_order_id: str
    symbol: str
    side: str
    currency: str
    quantity_decimal: str | None
    order_amount_decimal: str | None
    limit_price_decimal: str | None
    reference_price_decimal: str | None
    notional_decimal: str

    @classmethod
    def create(
        cls,
        signal: Signal,
        sizing: dict[str, Any],
        *,
        account_seq: str,
        snapshot_run_id: str,
        policy_hash: str,
        currency: str,
        reference_price: Any | None,
    ) -> "OrderIntent":
        if not account_seq.strip() or not snapshot_run_id.strip() or not policy_hash.strip():
            raise ValueError("order intent requires account, snapshot run, and policy hash")
        client_order_id = str(sizing.get("client_order_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", client_order_id):
            raise ValueError("client_order_id must be 1-36 alphanumeric, _ or - characters")
        symbol = signal.symbol_or_pair.strip().upper()
        side = signal.side.strip().upper()
        normalized_currency = currency.strip().upper()
        if not symbol or ":" in symbol:
            raise ValueError("order intent requires one concrete symbol")
        if side not in {"BUY", "SELL"}:
            raise ValueError("order intent side must be BUY or SELL")
        if not normalized_currency:
            raise ValueError("order intent currency is required")

        quantity = sizing.get("qty")
        amount = sizing.get("order_amount")
        if (quantity is None) == (amount is None):
            raise ValueError("order intent requires exactly one of qty or order_amount")
        quantity_text = (
            decimal_text(quantity, field="qty") if quantity is not None else None
        )
        amount_text = (
            decimal_text(amount, field="order_amount") if amount is not None else None
        )
        limit_price = sizing.get("limit_px")
        limit_text = (
            decimal_text(limit_price, field="limit_px")
            if limit_price is not None
            else None
        )
        reference_text = (
            decimal_text(reference_price, field="reference_price")
            if reference_price is not None
            else None
        )
        if amount_text is not None:
            notional = Decimal(amount_text)
        else:
            price_text = limit_text or reference_text
            if price_text is None:
                raise ValueError(
                    "quantity order intent requires a limit or current reference price"
                )
            notional = Decimal(quantity_text) * Decimal(price_text)

        return cls(
            account_seq=account_seq.strip(),
            snapshot_run_id=snapshot_run_id.strip(),
            policy_hash=policy_hash.strip(),
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            currency=normalized_currency,
            quantity_decimal=quantity_text,
            order_amount_decimal=amount_text,
            limit_price_decimal=limit_text,
            reference_price_decimal=reference_text,
            notional_decimal=format(notional, "f"),
        )

    @property
    def intent_hash(self) -> str:
        payload = json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
