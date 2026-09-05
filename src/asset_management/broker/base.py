from typing import TYPE_CHECKING, Mapping, Protocol

if TYPE_CHECKING:
    from asset_management.account.snapshots import AccountTruthSnapshot


class BrokerReadPort(Protocol):
    def collect_account_truth(self, *, runtime_run_id: str) -> "AccountTruthSnapshot": ...


class BrokerWritePort(Protocol):
    def submit(self, request: Mapping[str, object], *, idempotency_key: str) -> Mapping[str, object]: ...
    def cancel(self, broker_order_id: str, *, idempotency_key: str) -> Mapping[str, object]: ...
