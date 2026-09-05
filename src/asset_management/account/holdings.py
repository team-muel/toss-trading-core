from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Holding:
    account_id: str
    instrument_id: str
    quantity_decimal: str
    broker_response_id: str
