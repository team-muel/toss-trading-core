from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: str
    broker_order_id: str
    quantity_decimal: str
    amount_decimal: str
    source_response_id: str
