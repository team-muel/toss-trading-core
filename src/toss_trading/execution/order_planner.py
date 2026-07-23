from dataclasses import dataclass
from math import isfinite
import re
from typing import Any

from toss_trading.account.ledger import AccountLedger
from toss_trading.engines import Signal
from toss_trading.risk import RiskDecision


@dataclass(frozen=True)
class OrderPlan:
    client_order_id: str
    symbol: str
    side: str
    order_basis: str
    qty: float | None
    order_amount: float | None
    limit_px: float | None
    source_engine: str
    source_reason: str


class OrderPlanner:
    """Converts approved signals into paper order plans."""

    def create_plan(
        self,
        signal: Signal,
        sizing: dict[str, Any],
        *,
        risk_decision: RiskDecision,
        ledger: AccountLedger,
        account_seq: str,
        allowed_symbols: set[str] | frozenset[str],
    ) -> OrderPlan:
        if not risk_decision.approved:
            raise ValueError(f"risk decision rejected order plan: {risk_decision.reason}")
        if not account_seq.strip():
            raise ValueError("account_seq is required")
        if signal.symbol_or_pair not in allowed_symbols:
            raise ValueError("signal symbol is outside the approved universe")
        has_qty = "qty" in sizing and sizing["qty"] is not None
        has_amount = "order_amount" in sizing and sizing["order_amount"] is not None
        if has_qty == has_amount:
            raise ValueError("exactly one of qty or order_amount is required")
        client_order_id = str(sizing.get("client_order_id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", client_order_id):
            raise ValueError("client_order_id must be 1-36 alphanumeric, _ or - characters")
        if signal.side not in {"BUY", "SELL"}:
            raise ValueError("order side must be BUY or SELL")
        if ":" in signal.symbol_or_pair or not signal.symbol_or_pair.strip():
            raise ValueError("live/paper orders require one concrete symbol, not a pair")
        qty = float(sizing["qty"]) if has_qty else None
        amount = float(sizing["order_amount"]) if has_amount else None
        limit_px = sizing.get("limit_px")
        if qty is not None and (not isfinite(qty) or qty <= 0):
            raise ValueError("qty must be a finite positive number")
        if amount is not None and (not isfinite(amount) or amount <= 0):
            raise ValueError("order_amount must be a finite positive number")
        if limit_px is not None and (not isfinite(float(limit_px)) or float(limit_px) <= 0):
            raise ValueError("limit_px must be a finite positive number")
        ledger.reserve_client_order_id(
            account_seq=account_seq,
            client_order_id=client_order_id,
            request_payload={
                "symbol": signal.symbol_or_pair,
                "side": signal.side,
                "qty": qty,
                "order_amount": amount,
                "limit_px": limit_px,
            },
        )

        return OrderPlan(
            client_order_id=client_order_id,
            symbol=signal.symbol_or_pair,
            side=signal.side,
            order_basis="quantity" if has_qty else "amount",
            qty=qty,
            order_amount=amount,
            limit_px=float(limit_px) if limit_px is not None else None,
            source_engine=signal.engine,
            source_reason=signal.reason_code,
        )
