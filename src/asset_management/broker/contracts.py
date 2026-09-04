from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class BrokerSnapshot:
    account_id: str
    observed_at_utc: datetime
    balances: Mapping[str, str]
    positions: Sequence[Mapping[str, object]]
    open_orders: Sequence[Mapping[str, object]]
    source_response_ids: tuple[str, ...]
