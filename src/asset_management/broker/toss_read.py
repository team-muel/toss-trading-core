from toss_trading.broker.toss import TossReadOnlyAdapter

from asset_management.time.clock import Clock, SystemClock

from .contracts import BrokerSnapshot


class TossReadAdapter:
    """Anti-corruption layer around the existing verified Toss client."""

    def __init__(self, client: TossReadOnlyAdapter, clock: Clock | None = None) -> None:
        self._client = client
        self._clock = clock or SystemClock()

    def account_snapshot(self) -> BrokerSnapshot:
        holdings = self._client.get_holdings()
        orders = self._client.get_all_orders("OPEN")
        buying_power = self._client.get_buying_power()
        return BrokerSnapshot(
            account_id=self._client.credentials.account_seq or "",
            observed_at_utc=self._clock.now_utc(),
            balances=buying_power.body if isinstance(buying_power.body, dict) else {},
            positions=(holdings.body.get("result", []) if isinstance(holdings.body, dict) else []),
            open_orders=tuple(page.body for page in orders),
            source_response_ids=(holdings.raw_response_id, buying_power.raw_response_id, *(page.raw_response_id for page in orders)),
        )
