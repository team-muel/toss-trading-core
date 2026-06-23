from dataclasses import dataclass

from toss_trading.engines import Signal


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

    def create_plan(self, signal: Signal, sizing: dict) -> OrderPlan:
        has_qty = "qty" in sizing and sizing["qty"] is not None
        has_amount = "order_amount" in sizing and sizing["order_amount"] is not None
        if has_qty == has_amount:
            raise ValueError("exactly one of qty or order_amount is required")

        return OrderPlan(
            client_order_id=sizing["client_order_id"],
            symbol=signal.symbol_or_pair,
            side=signal.side,
            order_basis="quantity" if has_qty else "amount",
            qty=float(sizing["qty"]) if has_qty else None,
            order_amount=float(sizing["order_amount"]) if has_amount else None,
            limit_px=sizing.get("limit_px"),
            source_engine=signal.engine,
            source_reason=signal.reason_code,
        )
