from toss_trading.broker.toss import TossReadOnlyAdapter
from toss_trading.contracts.toss import (
    TossContractError,
    commission_rate_items,
    holdings_items,
    order_detail,
    orders_page,
    require_accounts,
    require_buying_power,
    require_sellable_quantity,
)

from asset_management.account.snapshots import AccountTruthSnapshot
from asset_management.account.consistency import compare_repeated_reads
from asset_management.broker.contracts import require_decimal_string, require_result
from asset_management.domain.errors import DataQualityError, ReconciliationError, UnknownBrokerState
from asset_management.time.clock import Clock, SystemClock

class TossReadAdapter:
    """Anti-corruption layer around the existing verified Toss client."""

    def __init__(self, client: TossReadOnlyAdapter, clock: Clock | None = None) -> None:
        if getattr(client, "ledger", None) is None and getattr(client, "raw_response_store", None) is None:
            raise ValueError("Toss read adapter requires an append-only raw-response ledger")
        self._client = client
        self._clock = clock or SystemClock()

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

    def collect_account_truth(self, *, runtime_run_id: str) -> AccountTruthSnapshot:
        try:
            return self._collect_account_truth(runtime_run_id=runtime_run_id)
        except TossContractError as exc:
            error = DataQualityError(str(exc))
            self._record_validation_failure(error)
            raise error from exc
        except (DataQualityError, UnknownBrokerState) as exc:
            self._record_validation_failure(exc)
            raise

    def _record_validation_failure(self, error: Exception) -> None:
        store = getattr(self._client, "raw_response_store", None)
        if store is not None:
            store.append_health(
                raw_response_id=None,
                source="toss",
                endpoint="account-truth-validation",
                status="BLOCKED",
                reason=f"{type(error).__name__}:{error}",
                observed_at=self._clock.now_utc(),
            )

    def _collect_account_truth(self, *, runtime_run_id: str) -> AccountTruthSnapshot:
        raw_ids: list[str] = []

        def keep(result):
            if not result.raw_response_id:
                raise DataQualityError(f"response has no raw evidence: {result.endpoint}")
            raw_ids.append(result.raw_response_id)
            return result

        accounts_result = keep(self._client.get_accounts())
        accounts = require_accounts(accounts_result.body)
        configured = self._client.credentials.account_seq
        account_ids = {str(item["accountSeq"]) for item in accounts}
        if configured is None:
            if len(account_ids) != 1:
                raise DataQualityError("account selection is ambiguous")
            configured = next(iter(account_ids))
        if configured not in account_ids:
            raise DataQualityError("configured account is absent from current account list")

        holdings_result = keep(self._client.get_holdings())
        holdings = holdings_items(holdings_result.body)
        for item in holdings:
            for field in ("quantity", "averagePurchasePrice", "lastPrice"):
                require_decimal_string(item[field], f"holdings.{field}")

        def read_orders(status: str) -> list[dict]:
            rows: list[dict] = []
            for result in self._client.get_all_orders(status):
                result = keep(result)
                page, _cursor, _has_next = orders_page(result.body, status=status)
                for item in page:
                    broker_status = str(item.get("status", ""))
                    if broker_status not in KNOWN_ORDER_STATUSES:
                        raise UnknownBrokerState(f"unknown Toss order status: {broker_status}")
                    if "quantity" in item:
                        require_decimal_string(item["quantity"], "orders.quantity")
                rows.extend(page)
            return rows

        open_orders = read_orders("OPEN")
        closed_orders = read_orders("CLOSED")
        details = []
        for item in (*open_orders, *closed_orders):
            order_id = str(item.get("orderId", ""))
            if not order_id:
                raise DataQualityError("order list item has no orderId")
            detail_result = keep(self._client.get_order(order_id))
            detail = order_detail(detail_result.body)
            details.append(detail)

        currencies = {"USD"} | {str(item["currency"]).upper() for item in holdings}
        buying_power_rows = []
        for currency in sorted(currencies):
            result = keep(self._client.get_buying_power(currency=currency))
            row = require_buying_power(result.body)
            require_decimal_string(row["cashBuyingPower"], "buyingPower.cashBuyingPower")
            buying_power_rows.append(row)

        sellable_rows = []
        for symbol in sorted({str(item["symbol"]) for item in holdings}):
            result = keep(self._client.get_sellable_quantity(symbol=symbol))
            row = dict(require_sellable_quantity(result.body))
            require_decimal_string(row["sellableQuantity"], "sellableQuantity")
            row.setdefault("symbol", symbol)
            sellable_rows.append(row)

        commission_result = keep(self._client.get_commissions())
        commissions = commission_rate_items(commission_result.body)
        for row in commissions:
            require_decimal_string(row["commissionRate"], "commissions.commissionRate")

        calendars = []
        for market in ("KR", "US"):
            result = keep(self._client.get_market_calendar(market))
            calendars.append(require_result(result.body, result.endpoint))

        symbols = sorted({str(item["symbol"]) for item in holdings})
        reference = []
        if symbols:
            result = keep(self._client.get_stocks(symbols))
            reference = require_result(result.body, result.endpoint)

        return AccountTruthSnapshot(
            runtime_run_id, configured, self._clock.now_utc(), tuple(accounts), tuple(holdings),
            tuple(open_orders), tuple(closed_orders), tuple(details), tuple(buying_power_rows),
            tuple(sellable_rows), tuple(commissions), tuple(calendars), reference, tuple(raw_ids),
        )

    def collect_consistent_account_truth(
        self, *, first_runtime_run_id: str, second_runtime_run_id: str
    ) -> AccountTruthSnapshot:
        first = self.collect_account_truth(runtime_run_id=first_runtime_run_id)
        second = self.collect_account_truth(runtime_run_id=second_runtime_run_id)
        result = compare_repeated_reads(first.comparison_payload(), second.comparison_payload())
        if result.status.name != "KNOWN":
            store = getattr(self._client, "raw_response_store", None)
            if store is not None:
                store.append_health(
                    raw_response_id=second.raw_response_ids[-1],
                    source="toss",
                    endpoint="account-truth-consistency",
                    status="BLOCKED",
                    reason=f"differing_sections:{','.join(result.differing_sections)}",
                    observed_at=self._clock.now_utc(),
                )
            raise ReconciliationError(
                f"repeated account reads conflict: {result.differing_sections}"
            )
        return second


KNOWN_ORDER_STATUSES = frozenset({
    "PENDING", "PENDING_CANCEL", "PENDING_REPLACE", "PARTIAL_FILLED", "FILLED",
    "CANCELED", "REJECTED", "CANCEL_REJECTED", "REPLACE_REJECTED", "REPLACED",
})
