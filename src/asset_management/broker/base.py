from typing import Mapping, Protocol

from .contracts import BrokerSnapshot


class BrokerReadPort(Protocol):
    def account_snapshot(self) -> BrokerSnapshot: ...


class BrokerWritePort(Protocol):
    def submit(self, request: Mapping[str, object], *, idempotency_key: str) -> Mapping[str, object]: ...
    def cancel(self, broker_order_id: str, *, idempotency_key: str) -> Mapping[str, object]: ...
