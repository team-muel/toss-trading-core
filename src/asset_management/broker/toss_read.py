from toss_trading.broker.toss import TossReadOnlyAdapter
from toss_trading.contracts.toss import (
    commission_rate_items,
    holdings_items,
    order_detail,
    orders_page,
    require_accounts,
    require_buying_power,
    require_sellable_quantity,
)

from asset_management.time.clock import Clock, SystemClock

from .contracts import BrokerSnapshot


class TossReadAdapter:
    """Anti-corruption layer around the existing verified Toss client."""

    def __init__(self, client: TossReadOnlyAdapter, clock: Clock | None = None) -> None:
        if client.ledger is None:
            raise ValueError("Toss read adapter requires an append-only raw-response ledger")
        self._client = client
        self._clock = clock or SystemClock()

    def account_snapshot(self) -> BrokerSnapshot:
        holdings = self._client.get_holdings()
        orders = self._client.get_all_orders("OPEN")
        buying_power = self._client.get_buying_power()
        holding_rows = holdings_items(holdings.body)
        order_rows = []
        for page in orders:
            rows, _cursor, _has_next = orders_page(page.body, status="OPEN")
            order_rows.extend(rows)
        buying_power_row = require_buying_power(buying_power.body)
        return BrokerSnapshot(
            account_id=self._client.credentials.account_seq or "",
            observed_at_utc=self._clock.now_utc(),
            balances=buying_power_row,
            positions=tuple(holding_rows),
            open_orders=tuple(order_rows),
            source_response_ids=(holdings.raw_response_id, buying_power.raw_response_id, *(page.raw_response_id for page in orders)),
        )

    def accounts(self) -> tuple[dict, ...]:
        return tuple(require_accounts(self._client.get_accounts().body))

    def holdings(self) -> tuple[dict, ...]:
        return tuple(holdings_items(self._client.get_holdings().body))

    def orders(self, status: str, **query: object) -> tuple[dict, ...]:
        if status not in {"OPEN", "CLOSED"}:
            raise ValueError("order status group must be OPEN or CLOSED")
        rows: list[dict] = []
        for page in self._client.get_all_orders(status, **query):
            page_rows, _cursor, _has_next = orders_page(page.body, status=status)
            rows.extend(page_rows)
        return tuple(rows)

    def order(self, order_id: str) -> dict:
        return order_detail(self._client.get_order(order_id).body)

    def buying_power(self, currency: str) -> dict:
        return require_buying_power(self._client.get_buying_power(currency=currency).body)

    def sellable_quantity(self, symbol: str) -> dict:
        return require_sellable_quantity(self._client.get_sellable_quantity(symbol=symbol).body)

    def commissions(self) -> tuple[dict, ...]:
        return tuple(commission_rate_items(self._client.get_commissions().body))

    def instrument_reference(self, symbols: list[str]):
        return self._client.get_stocks(symbols)

    def market_calendar(self, market_country: str, calendar_date: str | None = None):
        return self._client.get_market_calendar(market_country, calendar_date=calendar_date)
