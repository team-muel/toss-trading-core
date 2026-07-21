from .base import BrokerCapabilities
import copy
import hashlib
import json


class PaperBrokerAdapter:
    """Non-live broker used for research, simulation, and paper trading."""

    capabilities = BrokerCapabilities(
        market_data=False,
        order_entry=True,
        order_cancel=True,
        fills=False,
        balances=False,
        options_trading=False,
        margin_status=False,
    )

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}

    def submit_order(self, order: dict) -> dict:
        client_order_id = order["client_order_id"]
        payload_hash = hashlib.sha256(
            json.dumps(order, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        existing = self.orders.get(client_order_id)
        if existing is not None:
            if existing["_payload_hash"] != payload_hash:
                raise ValueError("client_order_id conflicts with a different paper order payload")
            return copy.deepcopy({key: value for key, value in existing.items() if key != "_payload_hash"})
        planned = {**order, "status": "paper_submitted"}
        planned["_payload_hash"] = payload_hash
        self.orders[client_order_id] = planned
        return copy.deepcopy({key: value for key, value in planned.items() if key != "_payload_hash"})

    def cancel_order(self, client_order_id: str) -> dict:
        order = self.orders.get(client_order_id)
        if order is None:
            return {"client_order_id": client_order_id, "status": "not_found"}
        order["status"] = "paper_cancelled"
        return copy.deepcopy({key: value for key, value in order.items() if key != "_payload_hash"})

    def get_balances(self) -> dict:
        return {"mode": "paper", "cash": None, "margin_used": None}

    def get_positions(self) -> list[dict]:
        return []
