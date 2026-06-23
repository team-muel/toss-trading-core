from .base import BrokerCapabilities


class PaperBrokerAdapter:
    """Non-live broker used for research, simulation, and paper trading."""

    capabilities = BrokerCapabilities(
        market_data=False,
        order_entry=True,
        order_cancel=True,
        fills=True,
        balances=True,
        options_trading=False,
        margin_status=False,
    )

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}

    def submit_order(self, order: dict) -> dict:
        client_order_id = order["client_order_id"]
        planned = {**order, "status": "paper_submitted"}
        self.orders[client_order_id] = planned
        return planned

    def cancel_order(self, client_order_id: str) -> dict:
        order = self.orders.get(client_order_id)
        if order is None:
            return {"client_order_id": client_order_id, "status": "not_found"}
        order["status"] = "paper_cancelled"
        return order

    def get_balances(self) -> dict:
        return {"mode": "paper", "cash": None, "margin_used": None}

    def get_positions(self) -> list[dict]:
        return []
