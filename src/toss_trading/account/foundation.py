from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from toss_trading.account.ledger import AccountLedger, AccountStateExplanation
from toss_trading.broker.toss import TossReadOnlyAdapter


@dataclass(frozen=True)
class FoundationSnapshotResult:
    accounts: int
    holdings: int
    open_orders: int
    closed_orders: int
    buying_power_rows: int
    commission_rows: int
    sellable_quantity_rows: int
    order_detail_rows: int
    execution_snapshot_rows: int
    execution_delta_rows: int
    explanation: AccountStateExplanation


def _orders_from_body(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    if "orderId" in body:
        return [body]
    result = body.get("result")
    if isinstance(result, dict):
        if "orderId" in result:
            return [result]
        orders = result.get("orders")
        if isinstance(orders, list):
            return [item for item in orders if isinstance(item, dict)]
    orders = body.get("orders")
    if isinstance(orders, list):
        return [item for item in orders if isinstance(item, dict)]
    return []


class FoundationSnapshotter:
    """Reads Toss account state, stores raw/normalized snapshots, and explains it."""

    def __init__(self, adapter: TossReadOnlyAdapter, ledger: AccountLedger) -> None:
        self.adapter = adapter
        self.ledger = ledger

    def snapshot(
        self,
        *,
        account_seq: str | None = None,
        include_sellable_quantity: bool = True,
        include_order_details: bool = True,
        buying_power_currency: str = "USD",
        max_order_pages: int = 20,
    ) -> FoundationSnapshotResult:
        accounts_result = self.adapter.get_accounts()
        accounts = self.ledger.ingest_accounts(
            accounts_result.body,
            raw_ref=accounts_result.raw_response_id,
        )
        credentials = getattr(self.adapter, "credentials", None)
        adapter_account_seq = getattr(credentials, "account_seq", None)
        resolved_account_seq = account_seq or adapter_account_seq
        if not resolved_account_seq:
            rows = self.ledger.conn.execute(
                """
                SELECT DISTINCT account_seq
                FROM account_snapshot
                ORDER BY account_seq
                """
            ).fetchall()
            if len(rows) != 1:
                raise RuntimeError(
                    "TOSS_ACCOUNT_SEQ is required when accounts list is empty or ambiguous"
                )
            resolved_account_seq = str(rows[0]["account_seq"])

        if credentials is not None and getattr(credentials, "account_seq", None) is None:
            object.__setattr__(credentials, "account_seq", resolved_account_seq)

        holdings_result = self.adapter.get_holdings()
        holdings = self.ledger.ingest_holdings(
            holdings_result.body,
            account_seq=resolved_account_seq,
            raw_ref=holdings_result.raw_response_id,
        )

        order_detail_rows = 0
        execution_snapshot_rows = 0
        execution_delta_rows = 0

        open_orders = 0
        closed_orders = 0
        order_results = []
        for result in self.adapter.get_all_orders(status="OPEN", max_pages=max_order_pages):
            order_results.append(result)
            open_orders += self.ledger.ingest_orders(
                result.body,
                account_seq=resolved_account_seq,
                raw_ref=result.raw_response_id,
            )
            snapshots, deltas = self.ledger.ingest_execution_snapshots(
                result.body,
                account_seq=resolved_account_seq,
                raw_ref=result.raw_response_id,
            )
            execution_snapshot_rows += snapshots
            execution_delta_rows += deltas

        for result in self.adapter.get_all_orders(status="CLOSED", max_pages=max_order_pages):
            order_results.append(result)
            closed_orders += self.ledger.ingest_orders(
                result.body,
                account_seq=resolved_account_seq,
                raw_ref=result.raw_response_id,
            )
            snapshots, deltas = self.ledger.ingest_execution_snapshots(
                result.body,
                account_seq=resolved_account_seq,
                raw_ref=result.raw_response_id,
            )
            execution_snapshot_rows += snapshots
            execution_delta_rows += deltas

        if include_order_details:
            seen_order_ids: set[str] = set()
            for result in order_results:
                for order in _orders_from_body(result.body):
                    order_id = str(order.get("orderId") or order.get("id") or "").strip()
                    if not order_id or order_id in seen_order_ids:
                        continue
                    seen_order_ids.add(order_id)
                    detail = self.adapter.get_order(order_id)
                    order_detail_rows += self.ledger.ingest_orders(
                        detail.body,
                        account_seq=resolved_account_seq,
                        raw_ref=detail.raw_response_id,
                    )
                    snapshots, deltas = self.ledger.ingest_execution_snapshots(
                        detail.body,
                        account_seq=resolved_account_seq,
                        raw_ref=detail.raw_response_id,
                    )
                    execution_snapshot_rows += snapshots
                    execution_delta_rows += deltas

        buying_power_result = self.adapter.get_buying_power(currency=buying_power_currency)
        buying_power_rows = self.ledger.ingest_buying_power(
            buying_power_result.body,
            account_seq=resolved_account_seq,
            raw_ref=buying_power_result.raw_response_id,
        )

        commissions_result = self.adapter.get_commissions()
        commission_rows = self.ledger.ingest_commissions(
            commissions_result.body,
            account_seq=resolved_account_seq,
            raw_ref=commissions_result.raw_response_id,
        )

        sellable_quantity_rows = 0
        if include_sellable_quantity:
            symbols = [
                row["symbol"]
                for row in self.ledger.conn.execute(
                    """
                    SELECT symbol
                    FROM holding_snapshot
                    WHERE account_seq = ?
                      AND ts = (
                        SELECT MAX(ts) FROM holding_snapshot WHERE account_seq = ?
                      )
                    ORDER BY symbol
                    """,
                    (resolved_account_seq, resolved_account_seq),
                ).fetchall()
            ]
            for symbol in symbols:
                result = self.adapter.get_sellable_quantity(symbol=symbol)
                sellable_quantity_rows += self.ledger.ingest_sellable_quantity(
                    result.body,
                    account_seq=resolved_account_seq,
                    raw_ref=result.raw_response_id,
                    fallback_symbol=symbol,
                )

        explanation = self.ledger.explain_account_state(resolved_account_seq)
        return FoundationSnapshotResult(
            accounts=accounts,
            holdings=holdings,
            open_orders=open_orders,
            closed_orders=closed_orders,
            buying_power_rows=buying_power_rows,
            commission_rows=commission_rows,
            sellable_quantity_rows=sellable_quantity_rows,
            order_detail_rows=order_detail_rows,
            execution_snapshot_rows=execution_snapshot_rows,
            execution_delta_rows=execution_delta_rows,
            explanation=explanation,
        )
