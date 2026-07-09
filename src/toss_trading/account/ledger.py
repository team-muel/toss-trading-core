from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from toss_trading.data.universe import InstrumentMapping


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_list(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if any(key in payload for key in ("orderId", "id", "broker_order_id")):
            return [payload]
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        result = payload.get("result")
        if isinstance(result, dict):
            nested = _as_list(result, *keys)
            if nested:
                return nested
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        for value in payload.values():
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _get(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_zero(value: Any) -> float:
    return _float(value) or 0.0


def _endpoint_like_pattern(endpoint: str) -> str:
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}%"


def _mask_account_no(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return f"{'*' * (len(text) - 4)}{text[-4:]}"


def _header_value(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


@dataclass(frozen=True)
class AccountStateExplanation:
    account_seq: str
    as_of: str
    holdings_count: int
    open_orders_count: int
    total_market_value: float
    buying_power_by_currency: dict[str, float]
    blockers: list[str]
    lines: list[str]

    def as_text(self) -> str:
        return "\n".join(self.lines)


class AccountLedger:
    """SQLite-backed foundation ledger for Toss account state snapshots."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def init_schema(self, schema_path: str | Path = "schemas/trading_ledger.sql") -> None:
        sql = Path(schema_path).read_text(encoding="utf-8")
        self.conn.executescript(sql)
        self.conn.commit()

    def load_instrument_mappings(self, mappings: list[InstrumentMapping]) -> None:
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO instrument_master (
              symbol_id, toss_symbol, ticker, vendor_symbol, occ_symbol, cik,
              asset_class, currency, timezone, mic, effective_from, effective_to, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.symbol_id,
                    item.toss_symbol,
                    item.ticker,
                    item.vendor_symbol,
                    item.occ_symbol or None,
                    item.cik or None,
                    item.asset_class,
                    item.currency,
                    item.timezone,
                    item.mic,
                    item.effective_from,
                    item.effective_to,
                    utc_now(),
                )
                for item in mappings
            ],
        )
        self.conn.commit()

    def save_raw_api_response(
        self,
        *,
        source: str,
        source_type: str,
        endpoint: str,
        http_method: str,
        body: Any,
        account_seq: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        request_payload: Any | None = None,
        headers: dict[str, str] | None = None,
        channel: str | None = None,
        ts: str | None = None,
    ) -> str:
        now = ts or utc_now()
        raw_id = str(uuid.uuid4())
        headers = headers or {}
        self.conn.execute(
            """
            INSERT INTO raw_api_response (
              id, ts, source, source_type, account_seq, channel, endpoint, http_method,
              request_id, request_hash, response_hash, status_code, rate_limit_limit,
              rate_limit_remaining, rate_limit_reset, body_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_id,
                now,
                source,
                source_type,
                account_seq,
                channel,
                endpoint,
                http_method.upper(),
                request_id,
                _json_hash(request_payload) if request_payload is not None else None,
                _json_hash(body),
                status_code,
                _header_value(headers, "X-RateLimit-Limit"),
                _header_value(headers, "X-RateLimit-Remaining"),
                _header_value(headers, "X-RateLimit-Reset"),
                json.dumps(body, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        self.conn.commit()
        return raw_id

    def record_source_health(
        self,
        *,
        source: str,
        channel: str,
        source_status: str,
        action: str | None = None,
        last_success_at: str | None = None,
        max_age_ms: int | None = None,
        heartbeat_timeout_ms: int | None = None,
        lag_ms: int | None = None,
        dropped_events: int = 0,
        ts: str | None = None,
    ) -> str:
        now = ts or utc_now()
        if source_status == "ok" and last_success_at is None:
            last_success_at = now
        health_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO source_health_snapshot (
              id, ts, source, channel, last_success_at, max_age_ms,
              heartbeat_timeout_ms, lag_ms, dropped_events, source_status,
              action, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                health_id,
                now,
                source,
                channel,
                last_success_at,
                max_age_ms,
                heartbeat_timeout_ms,
                lag_ms,
                dropped_events,
                source_status,
                action,
                now,
            ),
        )
        self.conn.commit()
        return health_id

    def latest_source_health(self, source: str, channel: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM source_health_snapshot
            WHERE source = ? AND channel = ?
            ORDER BY ts DESC, created_at DESC
            LIMIT 1
            """,
            (source, channel),
        ).fetchone()

    def _latest_successful_raw_ts(
        self,
        *,
        endpoint: str,
        account_seq: str | None = None,
    ) -> str | None:
        row = self.conn.execute(
            """
            SELECT MAX(ts) AS ts
            FROM raw_api_response
            WHERE (endpoint = ? OR endpoint LIKE ?)
              AND (status_code IS NULL OR status_code < 400)
              AND (? IS NULL OR account_seq = ?)
            """,
            (endpoint, _endpoint_like_pattern(endpoint), account_seq, account_seq),
        ).fetchone()
        return row["ts"] if row is not None else None

    def ingest_accounts(self, body: Any, *, raw_ref: str, ts: str | None = None) -> int:
        now = ts or utc_now()
        accounts = _as_list(body, "accounts", "results", "data")
        rows = []
        for item in accounts:
            account_seq = str(_get(item, "accountSeq", "account_seq", "seq", default="")).strip()
            if not account_seq:
                continue
            rows.append(
                (
                    str(uuid.uuid4()),
                    now,
                    account_seq,
                    _mask_account_no(_get(item, "accountNo", "accountNumber", "account_no")),
                    _get(item, "accountType", "account_type"),
                    "toss",
                    raw_ref,
                    now,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO account_snapshot (
              id, ts, account_seq, account_no_masked, account_type, broker, raw_response_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def ingest_holdings(
        self,
        body: Any,
        *,
        account_seq: str,
        raw_ref: str,
        ts: str | None = None,
    ) -> int:
        now = ts or utc_now()
        holdings = _as_list(body, "holdings", "stocks", "results", "data")
        rows = []
        for item in holdings:
            symbol = str(_get(item, "symbol", "ticker", "stockCode", default="")).strip().upper()
            quantity = _float(_get(item, "quantity", "qty", "holdingQuantity"))
            if not symbol or quantity is None:
                continue
            rows.append(
                (
                    str(uuid.uuid4()),
                    now,
                    account_seq,
                    symbol,
                    quantity,
                    _float(_get(item, "averagePurchasePrice", "avgPrice", "averagePrice")),
                    _float(_get(item, "lastPrice", "currentPrice", "price")),
                    _float(_get(item, "marketValue", "evaluatedAmount", "valuation")),
                    _float(_get(item, "profitLoss", "pnl", "profitAndLoss")),
                    _float(_get(item, "cost", "purchaseAmount")),
                    _get(item, "currency", default=None),
                    raw_ref,
                    now,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO holding_snapshot (
              id, ts, account_seq, symbol, quantity, average_purchase_price, last_price,
              market_value, profit_loss, cost, currency, raw_response_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def ingest_buying_power(
        self,
        body: Any,
        *,
        account_seq: str,
        raw_ref: str,
        ts: str | None = None,
    ) -> int:
        now = ts or utc_now()
        items = _as_list(body, "buyingPowers", "buying_power", "results", "data")
        if not items and isinstance(body, dict) and isinstance(body.get("result"), dict):
            items = [body["result"]]
        if not items:
            items = [body]
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            value = _float(_get(item, "cashBuyingPower", "cash_buying_power", "buyingPower"))
            currency = str(_get(item, "currency", default="USD")).upper()
            if value is None:
                continue
            rows.append((str(uuid.uuid4()), now, account_seq, currency, value, raw_ref, now))
        self.conn.executemany(
            """
            INSERT INTO buying_power_snapshot (
              id, ts, account_seq, currency, cash_buying_power, raw_response_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def ingest_orders(
        self,
        body: Any,
        *,
        account_seq: str,
        raw_ref: str,
        ts: str | None = None,
    ) -> int:
        now = ts or utc_now()
        orders = _as_list(body, "orders", "results", "data")
        rows = []
        for item in orders:
            broker_order_id = str(_get(item, "orderId", "id", default="")).strip()
            symbol = str(_get(item, "symbol", "ticker", default="")).strip().upper()
            status = str(_get(item, "status", default="UNKNOWN")).strip()
            if not broker_order_id or not symbol:
                continue
            execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
            rows.append(
                (
                    str(uuid.uuid4()),
                    now,
                    account_seq,
                    broker_order_id,
                    _get(item, "clientOrderId", "client_order_id"),
                    symbol,
                    _get(item, "side"),
                    _get(item, "orderType", "order_type"),
                    _get(item, "timeInForce", "time_in_force"),
                    status,
                    _float(_get(item, "quantity")),
                    _float(_get(item, "orderAmount", "order_amount")),
                    _float(_get(item, "price")),
                    _float(_get(execution, "filledQuantity")),
                    _float(_get(execution, "filledAmount")),
                    _float(_get(execution, "averageFilledPrice")),
                    _float(_get(execution, "commission")),
                    _float(_get(execution, "tax")),
                    _get(execution, "settlementDate"),
                    _get(item, "orderedAt"),
                    _get(item, "canceledAt"),
                    raw_ref,
                    now,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO broker_order_snapshot (
              id, ts, account_seq, broker_order_id, client_order_id, symbol, side,
              order_type, time_in_force, status, quantity, order_amount, price,
              cumulative_filled_qty, cumulative_filled_amount, average_filled_price,
              cumulative_commission, cumulative_tax, settlement_date, ordered_at,
              canceled_at, raw_response_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def ingest_execution_snapshots(
        self,
        body: Any,
        *,
        account_seq: str,
        raw_ref: str,
        ts: str | None = None,
    ) -> tuple[int, int]:
        now = ts or utc_now()
        orders = _as_list(body, "orders", "results", "data")
        inserted_snapshots = 0
        inserted_deltas = 0
        for item in orders:
            broker_order_id = str(_get(item, "orderId", "id", default="")).strip()
            if not broker_order_id:
                continue
            execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
            filled_qty = _float(_get(execution, "filledQuantity"))
            filled_amount = _float(_get(execution, "filledAmount"))
            if filled_qty is None or filled_amount is None:
                continue
            order_id = broker_order_id
            previous = self.conn.execute(
                """
                SELECT *
                FROM execution_snapshot_log
                WHERE account_seq = ? AND order_id = ?
                ORDER BY snapshot_seq DESC
                LIMIT 1
                """,
                (account_seq, order_id),
            ).fetchone()
            cumulative_commission = _float_or_zero(_get(execution, "commission"))
            cumulative_tax = _float_or_zero(_get(execution, "tax"))
            order_status = str(_get(item, "status", default="UNKNOWN"))
            average_filled_price = _float(_get(execution, "averageFilledPrice"))
            settlement_date = _get(execution, "settlementDate")

            if previous is not None:
                unchanged = (
                    float(previous["cumulative_filled_qty"]) == filled_qty
                    and float(previous["cumulative_filled_amount"]) == filled_amount
                    and float(previous["cumulative_commission"] or 0) == cumulative_commission
                    and float(previous["cumulative_tax"] or 0) == cumulative_tax
                    and previous["order_status"] == order_status
                    and previous["settlement_date"] == settlement_date
                )
                if unchanged:
                    continue
                delta_qty = filled_qty - float(previous["cumulative_filled_qty"])
                delta_amount = filled_amount - float(previous["cumulative_filled_amount"])
                delta_commission = cumulative_commission - float(
                    previous["cumulative_commission"] or 0
                )
                delta_tax = cumulative_tax - float(previous["cumulative_tax"] or 0)
                if min(delta_qty, delta_amount, delta_commission, delta_tax) < 0:
                    self.conn.execute(
                        """
                        INSERT INTO broker_reconciliation_log (
                          id, ts, account_seq, item_type, broker_value, internal_value,
                          difference, status, action_required, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            now,
                            account_seq,
                            "execution_snapshot",
                            json.dumps(item, ensure_ascii=False, sort_keys=True),
                            json.dumps(dict(previous), ensure_ascii=False, sort_keys=True),
                            "negative_execution_delta",
                            "BLOCK",
                            "inspect_order_detail_before_new_orders",
                            now,
                        ),
                    )
                    self.conn.commit()
                    continue
                snapshot_seq = int(previous["snapshot_seq"]) + 1
                from_snapshot_id = previous["id"]
            else:
                delta_qty = filled_qty
                delta_amount = filled_amount
                delta_commission = cumulative_commission
                delta_tax = cumulative_tax
                snapshot_seq = 1
                from_snapshot_id = None

            snapshot_id = str(uuid.uuid4())
            self.conn.execute(
                """
                INSERT INTO execution_snapshot_log (
                  id, ts, account_seq, order_id, broker_order_id, snapshot_seq,
                  order_status, cumulative_filled_qty, cumulative_filled_amount,
                  average_filled_price, cumulative_commission, cumulative_tax,
                  settlement_date, raw_snapshot_ref, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    now,
                    account_seq,
                    order_id,
                    broker_order_id,
                    snapshot_seq,
                    order_status,
                    filled_qty,
                    filled_amount,
                    average_filled_price,
                    cumulative_commission,
                    cumulative_tax,
                    settlement_date,
                    raw_ref,
                    now,
                ),
            )
            inserted_snapshots += 1
            if delta_qty or delta_amount or delta_commission or delta_tax:
                self.conn.execute(
                    """
                    INSERT INTO execution_delta_log (
                      id, ts, account_seq, order_id, broker_order_id, from_snapshot_id,
                      to_snapshot_id, delta_filled_qty, delta_filled_amount,
                      delta_commission, delta_tax, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        now,
                        account_seq,
                        order_id,
                        broker_order_id,
                        from_snapshot_id,
                        snapshot_id,
                        delta_qty,
                        delta_amount,
                        delta_commission,
                        delta_tax,
                        now,
                    ),
                )
                inserted_deltas += 1
        self.conn.commit()
        return inserted_snapshots, inserted_deltas

    def ingest_sellable_quantity(
        self,
        body: Any,
        *,
        account_seq: str,
        raw_ref: str,
        fallback_symbol: str | None = None,
        ts: str | None = None,
    ) -> int:
        now = ts or utc_now()
        items = _as_list(body, "sellableQuantities", "results", "data") or [body]
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = str(
                _get(item, "symbol", "ticker", "stockCode", default=fallback_symbol or "")
            ).strip().upper()
            quantity = _float(
                _get(item, "sellableQuantity", "sellable_quantity", "quantity")
            )
            if not symbol or quantity is None:
                continue
            rows.append((str(uuid.uuid4()), now, account_seq, symbol, quantity, raw_ref, now))
        self.conn.executemany(
            """
            INSERT INTO sellable_quantity_snapshot (
              id, ts, account_seq, symbol, sellable_quantity, raw_response_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def ingest_commissions(
        self,
        body: Any,
        *,
        account_seq: str,
        raw_ref: str,
        ts: str | None = None,
    ) -> int:
        now = ts or utc_now()
        items = _as_list(body, "commissions", "results", "data") or [body]
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            amount = _float(
                _get(item, "commission", "commissionAmount", "commission_amount")
            )
            rows.append(
                (
                    str(uuid.uuid4()),
                    now,
                    account_seq,
                    _get(item, "market"),
                    _get(item, "symbol", "ticker"),
                    _get(item, "side"),
                    _float(_get(item, "orderAmount", "order_amount")),
                    amount,
                    _get(item, "currency"),
                    raw_ref,
                    now,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO commission_snapshot (
              id, ts, account_seq, market, symbol, side, order_amount,
              commission_amount, currency, raw_response_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def explain_account_state(self, account_seq: str) -> AccountStateExplanation:
        holdings_cutoff = self._latest_successful_raw_ts(
            endpoint="/api/v1/holdings",
            account_seq=account_seq,
        )
        open_orders_cutoff = self._latest_successful_raw_ts(
            endpoint="/api/v1/orders?status=OPEN",
            account_seq=account_seq,
        )
        buying_power_cutoff = self._latest_successful_raw_ts(
            endpoint="/api/v1/buying-power",
            account_seq=account_seq,
        )
        holdings = self.conn.execute(
            """
            SELECT symbol, quantity, market_value, currency, ts
            FROM holding_snapshot
            WHERE account_seq = ?
              AND (? IS NULL OR ts >= ?)
              AND ts = (
                SELECT MAX(ts)
                FROM holding_snapshot
                WHERE account_seq = ?
                  AND (? IS NULL OR ts >= ?)
              )
            ORDER BY symbol
            """,
            (
                account_seq,
                holdings_cutoff,
                holdings_cutoff,
                account_seq,
                holdings_cutoff,
                holdings_cutoff,
            ),
        ).fetchall()
        buying_power_rows = self.conn.execute(
            """
            SELECT currency, cash_buying_power, ts
            FROM buying_power_snapshot
            WHERE account_seq = ?
              AND (? IS NULL OR ts >= ?)
              AND ts = (
                SELECT MAX(ts)
                FROM buying_power_snapshot
                WHERE account_seq = ?
                  AND (? IS NULL OR ts >= ?)
              )
            ORDER BY currency
            """,
            (
                account_seq,
                buying_power_cutoff,
                buying_power_cutoff,
                account_seq,
                buying_power_cutoff,
                buying_power_cutoff,
            ),
        ).fetchall()
        open_orders = self.conn.execute(
            """
            SELECT COUNT(DISTINCT current.broker_order_id) AS count
            FROM broker_order_snapshot current
            WHERE current.account_seq = ?
              AND (? IS NULL OR current.ts >= ?)
              AND status IN ('PENDING', 'PENDING_CANCEL', 'PENDING_REPLACE', 'PARTIAL_FILLED')
              AND NOT EXISTS (
                SELECT 1
                FROM broker_order_snapshot newer
                WHERE newer.account_seq = current.account_seq
                  AND newer.broker_order_id = current.broker_order_id
                  AND newer.ts > current.ts
              )
            """,
            (account_seq, open_orders_cutoff, open_orders_cutoff),
        ).fetchone()["count"]

        total_market_value = sum(float(row["market_value"] or 0) for row in holdings)
        buying_power = {
            row["currency"]: float(row["cash_buying_power"] or 0) for row in buying_power_rows
        }
        blockers: list[str] = []
        if not holdings and holdings_cutoff is None:
            blockers.append("missing_latest_holdings_snapshot")
        if not buying_power and buying_power_cutoff is None:
            blockers.append("missing_latest_buying_power_snapshot")

        timestamps = (
            [row["ts"] for row in holdings]
            + [row["ts"] for row in buying_power_rows]
            + [
                ts
                for ts in (holdings_cutoff, open_orders_cutoff, buying_power_cutoff)
                if ts is not None
            ]
        )
        as_of = max(timestamps) if timestamps else utc_now()
        lines = [
            f"account_seq={account_seq}",
            f"as_of={as_of}",
            f"holdings_count={len(holdings)}",
            f"total_holding_market_value={total_market_value:.2f}",
            f"open_orders_count={open_orders}",
            f"buying_power_by_currency={buying_power}",
            f"blockers={blockers or ['none']}",
        ]
        return AccountStateExplanation(
            account_seq=account_seq,
            as_of=as_of,
            holdings_count=len(holdings),
            open_orders_count=int(open_orders),
            total_market_value=total_market_value,
            buying_power_by_currency=buying_power,
            blockers=blockers,
            lines=lines,
        )
