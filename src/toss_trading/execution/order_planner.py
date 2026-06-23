from dataclasses import dataclass

from toss_trading.engines import Signal


@dataclass(frozen=True)
class OrderPlan:
    client_order_id: str
    symbol: str
    side: str
    qty: float
    limit_px: float | None
    source_engine: str
    source_reason: str


class OrderPlanner:
    """Converts approved signals into paper order plans."""

    def create_plan(self, signal: Signal, sizing: dict) -> OrderPlan:
        return OrderPlan(
            client_order_id=sizing["client_order_id"],
            symbol=signal.symbol_or_pair,
            side=signal.side,
            qty=float(sizing["qty"]),
            limit_px=sizing.get("limit_px"),
            source_engine=signal.engine,
            source_reason=signal.reason_code,
        )
