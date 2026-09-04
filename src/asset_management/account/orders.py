from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str
    status: str
    source_response_id: str
