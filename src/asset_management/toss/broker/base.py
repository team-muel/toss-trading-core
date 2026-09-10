from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BrokerCapabilities:
    market_data: bool = False
    order_entry: bool = False
    order_cancel: bool = False
    fills: bool = False
    balances: bool = False
    conditional_order_entry: bool = False
    conditional_order_modify: bool = False
    options_trading: bool = False
    margin_status: bool = False


class BrokerAdapter(Protocol):
    """Broker boundary. Live adapters must satisfy these methods explicitly."""

    @property
    def capabilities(self) -> BrokerCapabilities:
        ...

    def submit_order(self, order: dict) -> dict:
        ...

    def cancel_order(self, client_order_id: str) -> dict:
        ...

    def get_balances(self) -> dict:
        ...

    def get_positions(self) -> list[dict]:
        ...
