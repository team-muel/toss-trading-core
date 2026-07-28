from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import Any, Protocol

from toss_trading.account.ledger import AccountLedger, AccountStateExplanation
from toss_trading.contracts import require_accounts


class ReadOnlyAccountAdapter(Protocol):
    run_id: str | None

    def get_accounts(self): ...

    def get_holdings(self): ...

    def get_all_orders(self, status: str, **query): ...

    def get_order(self, order_id: str): ...

    def get_buying_power(self, **query): ...

    def get_commissions(self, **query): ...

    def get_sellable_quantity(self, **query): ...


@dataclass(frozen=True)
class FoundationSnapshotResult:
    run_id: str
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
    cash_event_rows: int
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

    def __init__(self, adapter: ReadOnlyAccountAdapter, ledger: AccountLedger) -> None:
        self.adapter = adapter
        self.ledger = ledger

    def snapshot(
        self,
        *,
        account_seq: str | None = None,
        include_sellable_quantity: bool = True,
        include_order_details: bool = True,
        include_closed_orders: bool = False,
        buying_power_currency: str | list[str] | tuple[str, ...] = "USD",
        max_order_pages: int = 20,
        max_order_details: int = 20,
        target_order_id: str | None = None,
        policy_hash: str | None = None,
        code_revision: str | None = None,
    ) -> FoundationSnapshotResult:
        run_id = self.ledger.begin_snapshot_run(
            account_seq=account_seq,
            target_order_id=target_order_id,
            policy_hash=policy_hash,
            code_revision=code_revision,
        )
        try:
            if hasattr(self.adapter, "run_id"):
                self.adapter.run_id = run_id
            accounts_result = self.adapter.get_accounts()
            account_items = require_accounts(accounts_result.body)
            accounts = self.ledger.ingest_accounts(
                accounts_result.body,
                raw_ref=accounts_result.raw_response_id,
                run_id=run_id,
            )
            current_account_seqs = {
                str(item["accountSeq"]).strip() for item in account_items if item.get("accountSeq")
            }
            if not current_account_seqs:
                raise RuntimeError("Toss accounts response did not contain an accountSeq")
            credentials = getattr(self.adapter, "credentials", None)
            adapter_account_seq = getattr(credentials, "account_seq", None)
            resolved_account_seq = account_seq or adapter_account_seq
            if not resolved_account_seq:
                if len(current_account_seqs) != 1:
                    raise RuntimeError(
                        "TOSS_ACCOUNT_SEQ is required when the current accounts response is ambiguous"
                    )
                resolved_account_seq = next(iter(current_account_seqs))
            if resolved_account_seq not in current_account_seqs:
                raise RuntimeError(
                    "requested account_seq is not present in the current Toss accounts response"
                )
            if (
                credentials is not None
                and adapter_account_seq != resolved_account_seq
            ):
                if not hasattr(self.adapter, "with_account"):
                    raise RuntimeError("adapter account differs from requested account_seq")
                self.adapter = self.adapter.with_account(resolved_account_seq)
                if hasattr(self.adapter, "run_id"):
                    self.adapter.run_id = run_id

            holdings_result = self.adapter.get_holdings()
            holdings = self.ledger.ingest_holdings(
                holdings_result.body,
                account_seq=resolved_account_seq,
                raw_ref=holdings_result.raw_response_id,
                run_id=run_id,
            )

            order_detail_rows = 0
            execution_snapshot_rows = 0
            execution_delta_rows = 0
            open_orders = 0
            open_order_results = []
            for result in self.adapter.get_all_orders(status="OPEN", max_pages=max_order_pages):
                open_order_results.append(result)
                open_orders += self.ledger.ingest_orders(
                    result.body,
                    account_seq=resolved_account_seq,
                    raw_ref=result.raw_response_id,
                    run_id=run_id,
                    status_group="OPEN",
                )
                snapshots, deltas = self.ledger.ingest_execution_snapshots(
                    result.body,
                    account_seq=resolved_account_seq,
                    raw_ref=result.raw_response_id,
                    run_id=run_id,
                    status_group="OPEN",
                )
                execution_snapshot_rows += snapshots
                execution_delta_rows += deltas

            closed_orders = 0
            closed_order_results = []
            if include_closed_orders:
                for result in self.adapter.get_all_orders(
                    status="CLOSED", max_pages=max_order_pages
                ):
                    closed_order_results.append(result)
                    closed_orders += self.ledger.ingest_orders(
                        result.body,
                        account_seq=resolved_account_seq,
                        raw_ref=result.raw_response_id,
                        run_id=run_id,
                        status_group="CLOSED",
                    )
                    snapshots, deltas = self.ledger.ingest_execution_snapshots(
                        result.body,
                        account_seq=resolved_account_seq,
                        raw_ref=result.raw_response_id,
                        run_id=run_id,
                        status_group="CLOSED",
                    )
                    execution_snapshot_rows += snapshots
                    execution_delta_rows += deltas

            if include_order_details:
                detail_limit = max(0, max_order_details)
                seen_order_ids: set[str] = set()
                detail_order_ids = [target_order_id] if target_order_id else []
                for result in closed_order_results:
                    detail_order_ids.extend(
                        str(order.get("orderId") or "").strip()
                        for order in _orders_from_body(result.body)
                    )
                for result in open_order_results:
                    detail_order_ids.extend(
                        str(order.get("orderId") or "").strip()
                        for order in _orders_from_body(result.body)
                    )
                for order_id in detail_order_ids:
                    if order_detail_rows >= detail_limit:
                        break
                    if not order_id or order_id in seen_order_ids:
                        continue
                    seen_order_ids.add(order_id)
                    detail = self.adapter.get_order(order_id)
                    order_detail_rows += self.ledger.ingest_orders(
                        detail.body,
                        account_seq=resolved_account_seq,
                        raw_ref=detail.raw_response_id,
                        run_id=run_id,
                    )
                    snapshots, deltas = self.ledger.ingest_execution_snapshots(
                        detail.body,
                        account_seq=resolved_account_seq,
                        raw_ref=detail.raw_response_id,
                        run_id=run_id,
                    )
                    execution_snapshot_rows += snapshots
                    execution_delta_rows += deltas

            configured_currencies = (
                {buying_power_currency.strip().upper()}
                if isinstance(buying_power_currency, str)
                else {
                    str(currency).strip().upper()
                    for currency in buying_power_currency
                    if str(currency).strip()
                }
            )
            holding_currencies = {
                str(row["currency"]).strip().upper()
                for row in self.ledger.conn.execute(
                    """
                    SELECT DISTINCT currency
                    FROM holding_snapshot
                    WHERE account_seq = ? AND run_id = ? AND currency IS NOT NULL
                    """,
                    (resolved_account_seq, run_id),
                ).fetchall()
                if str(row["currency"]).strip()
            }
            buying_power_rows = 0
            for currency in sorted(configured_currencies | holding_currencies):
                buying_power_result = self.adapter.get_buying_power(currency=currency)
                buying_power_rows += self.ledger.ingest_buying_power(
                    buying_power_result.body,
                    account_seq=resolved_account_seq,
                    raw_ref=buying_power_result.raw_response_id,
                    run_id=run_id,
                )
            commissions_result = self.adapter.get_commissions()
            commission_rows = self.ledger.ingest_commissions(
                commissions_result.body,
                account_seq=resolved_account_seq,
                raw_ref=commissions_result.raw_response_id,
                run_id=run_id,
            )

            sellable_quantity_rows = 0
            if include_sellable_quantity:
                symbols = [
                    row["symbol"]
                    for row in self.ledger.conn.execute(
                        """
                        SELECT symbol FROM holding_snapshot
                        WHERE account_seq = ? AND run_id = ?
                        ORDER BY symbol
                        """,
                        (resolved_account_seq, run_id),
                    ).fetchall()
                ]
                for symbol in symbols:
                    result = self.adapter.get_sellable_quantity(symbol=symbol)
                    sellable_quantity_rows += self.ledger.ingest_sellable_quantity(
                        result.body,
                        account_seq=resolved_account_seq,
                        raw_ref=result.raw_response_id,
                        fallback_symbol=symbol,
                        run_id=run_id,
                    )

            # Backfill every unposted execution delta, including a previous run
            # that failed after persisting a fill but before cash posting.
            cash_event_rows = self.ledger.post_execution_cash_events(
                account_seq=resolved_account_seq,
            )
            self.ledger.finish_snapshot_run(run_id, account_seq=resolved_account_seq)
            explanation = self.ledger.explain_account_state(resolved_account_seq, run_id=run_id)
            return FoundationSnapshotResult(
                run_id=run_id,
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
                cash_event_rows=cash_event_rows,
                explanation=explanation,
            )
        except Exception as exc:
            self.ledger.fail_snapshot_run(run_id, str(exc))
            raise
