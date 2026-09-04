from typing import Mapping

from asset_management.domain.errors import NoTrade


class DisabledTossWriteAdapter:
    def submit(self, request: Mapping[str, object], *, idempotency_key: str) -> Mapping[str, object]:
        raise NoTrade("Toss live order entry is not enabled")

    def cancel(self, broker_order_id: str, *, idempotency_key: str) -> Mapping[str, object]:
        raise NoTrade("Toss live order cancellation is not enabled")
