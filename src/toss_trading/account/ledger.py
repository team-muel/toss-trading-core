from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from toss_trading.data.universe import InstrumentMapping
from toss_trading.contracts import (
    commission_rate_items,
    holdings_items,
    order_detail,
    orders_page,
    require_accounts,
    require_buying_power,
    require_sellable_quantity,
)


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


def _decimal_text(value: Any) -> str | None:
    """Return an exact, finite decimal representation without binary rounding."""

    if value is None or value == "":
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not decimal.is_finite():
        return None
    return format(decimal, "f")


def _sum_decimal_text(*values: Any) -> str | None:
    decimals: list[Decimal] = []
    for value in values:
        text = _decimal_text(value)
        if text is not None:
            decimals.append(Decimal(text))
    if not decimals:
        return None
    return format(sum(decimals, Decimal("0")), "f")


def _float_or_zero(value: Any) -> float:
    return _float(value) or 0.0


def _same_optional_float(left: Any, right: float | None) -> bool:
    left_float = _float(left)
    if left_float is None or right is None:
        return left_float is None and right is None
    return left_float == right


def _endpoint_like_pattern(endpoint: str) -> str:
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}%"


def _official_orders(body: Any, *, status_group: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or "result" not in body:
        raise ValueError("/api/v1/orders: expected result envelope")
    result = body["result"]
    if isinstance(result, dict) and "orderId" in result:
        return [order_detail(body)]
    if status_group not in {"OPEN", "CLOSED"}:
        raise ValueError("/api/v1/orders: status group is required for a list response")
    orders, _, _ = orders_page(body, status=status_group)
    return orders


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
    run_id: str | None
    holdings_count: int
    open_orders_count: int
    market_value_by_currency: dict[str, str]
    buying_power_by_currency: dict[str, str]
    blockers: list[str]
    lines: list[str]

    def as_text(self) -> str:
        return "\n".join(self.lines)


class AccountLedger:
    """SQLite-backed foundation ledger for Toss account state snapshots."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.current_run_id: str | None = None

    def close(self) -> None:
        self.conn.close()

    def init_schema(self, schema_path: str | Path = "schemas/trading_ledger.sql") -> None:
        sql = Path(schema_path).read_text(encoding="utf-8")
        self.conn.executescript(sql)
        self._migrate_schema()
        self.conn.commit()

    def _migrate_schema(self) -> None:
        """Apply additive migrations for databases created by older foundations."""

        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version < 1:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshot_run (
                  run_id TEXT PRIMARY KEY,
                  account_seq TEXT,
                  target_order_id TEXT,
                  started_at TEXT NOT NULL,
                  completed_at TEXT,
                  status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED')),
                  failure_reason TEXT,
                  schema_version INTEGER NOT NULL,
                  normalizer_version TEXT NOT NULL,
                  policy_hash TEXT,
                  code_revision TEXT,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commission_rate_schedule_snapshot (
                  id TEXT PRIMARY KEY,
                  run_id TEXT,
                  ts TEXT NOT NULL,
                  account_seq TEXT NOT NULL,
                  market_country TEXT NOT NULL,
                  commission_rate_decimal TEXT NOT NULL,
                  start_date TEXT,
                  end_date TEXT,
                  raw_response_ref TEXT,
                  created_at TEXT NOT NULL
                );
                """
            )
            for table, columns in {
                "raw_api_response": ["run_id TEXT"],
                "source_health_snapshot": ["run_id TEXT"],
                "account_snapshot": ["run_id TEXT"],
                "holding_snapshot": [
                    "run_id TEXT",
                    "quantity_decimal TEXT",
                    "average_purchase_price_decimal TEXT",
                    "last_price_decimal TEXT",
                    "market_value_decimal TEXT",
                    "profit_loss_decimal TEXT",
                    "cost_decimal TEXT",
                ],
                "broker_order_snapshot": [
                    "run_id TEXT",
                    "currency TEXT",
                    "quantity_decimal TEXT",
                    "order_amount_decimal TEXT",
                    "price_decimal TEXT",
                    "cumulative_filled_qty_decimal TEXT",
                    "cumulative_filled_amount_decimal TEXT",
                    "average_filled_price_decimal TEXT",
                    "cumulative_commission_decimal TEXT",
                    "cumulative_tax_decimal TEXT",
                ],
                "buying_power_snapshot": ["run_id TEXT", "cash_buying_power_decimal TEXT"],
                "sellable_quantity_snapshot": ["run_id TEXT", "sellable_quantity_decimal TEXT"],
                "commission_snapshot": ["run_id TEXT"],
                "execution_snapshot_log": [
                    "run_id TEXT",
                    "currency TEXT",
                    "cumulative_filled_qty_decimal TEXT",
                    "cumulative_filled_amount_decimal TEXT",
                    "average_filled_price_decimal TEXT",
                    "cumulative_commission_decimal TEXT",
                    "cumulative_tax_decimal TEXT",
                ],
                "execution_delta_log": [
                    "run_id TEXT",
                    "currency TEXT",
                    "delta_filled_qty_decimal TEXT",
                    "delta_filled_amount_decimal TEXT",
                    "delta_commission_decimal TEXT",
                    "delta_tax_decimal TEXT",
                ],
                "client_order_id_registry": ["request_hash TEXT"],
            }.items():
                existing = {
                    row["name"]
                    for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for column in columns:
                    name = column.split()[0]
                    if name not in existing:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            self.conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshot_run_complete
                  ON snapshot_run(status, completed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_raw_run_endpoint
                  ON raw_api_response(run_id, account_seq, endpoint, status_code);
                CREATE INDEX IF NOT EXISTS idx_holding_run_account
                  ON holding_snapshot(run_id, account_seq, symbol);
                CREATE INDEX IF NOT EXISTS idx_order_run_account
                  ON broker_order_snapshot(run_id, account_seq, broker_order_id);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_holding_per_completed_run
                  ON holding_snapshot(run_id, account_seq, symbol)
                  WHERE run_id IS NOT NULL;
                """
            )
            self.conn.execute("PRAGMA user_version = 1")

    def begin_snapshot_run(
        self,
        *,
        account_seq: str | None,
        target_order_id: str | None = None,
        policy_hash: str | None = None,
        code_revision: str | None = None,
    ) -> str:
        if self.current_run_id is not None:
            raise RuntimeError("a snapshot run is already active")
        run_id = str(uuid.uuid4())
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO snapshot_run (
              run_id, account_seq, target_order_id, started_at, status, schema_version,
              normalizer_version, policy_hash, code_revision, created_at
            ) VALUES (?, ?, ?, ?, 'RUNNING', 1, 'toss-openapi-1.2.4', ?, ?, ?)
            """,
            (run_id, account_seq, target_order_id, now, policy_hash, code_revision, now),
        )
        self.conn.commit()
        self.current_run_id = run_id
        return run_id

    def finish_snapshot_run(self, run_id: str, *, account_seq: str) -> None:
        now = utc_now()
        cursor = self.conn.execute(
            """
            UPDATE snapshot_run
            SET account_seq = ?, completed_at = ?, status = 'COMPLETE'
            WHERE run_id = ? AND status = 'RUNNING'
            """,
            (account_seq, now, run_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"snapshot run cannot be completed: {run_id}")
        self.conn.commit()
        self.current_run_id = None

    def fail_snapshot_run(self, run_id: str, reason: str) -> None:
        self.conn.execute(
            """
            UPDATE snapshot_run
            SET completed_at = ?, status = 'FAILED', failure_reason = ?
            WHERE run_id = ? AND status = 'RUNNING'
            """,
            (utc_now(), reason[:1000], run_id),
        )
        self.conn.commit()
        self.current_run_id = None

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

    def reserve_client_order_id(
        self,
        *,
        account_seq: str,
        client_order_id: str,
        request_payload: dict[str, Any],
    ) -> bool:
        """Atomically reserve an idempotency key.

        Returns True for an already-reserved identical payload and raises for a
        conflicting payload.  The caller may then safely replay the same broker
        request, never invent a new id after an unknown outcome.
        """

        request_hash = _json_hash(request_payload)
        row = self.conn.execute(
            """
            SELECT request_hash
            FROM client_order_id_registry
            WHERE client_order_id = ? AND account_seq = ?
            """,
            (client_order_id, account_seq),
        ).fetchone()
        if row is not None:
            if row["request_hash"] == request_hash:
                return True
            raise ValueError("client_order_id is already reserved for a different payload")
        self.conn.execute(
            """
            INSERT INTO client_order_id_registry (
              client_order_id, account_seq, first_used_at, reuse_forbidden, request_hash
            ) VALUES (?, ?, ?, 1, ?)
            """,
            (client_order_id, account_seq, utc_now(), request_hash),
        )
        self.conn.commit()
        return False

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
        run_id: str | None = None,
    ) -> str:
        now = ts or utc_now()
        raw_id = str(uuid.uuid4())
        headers = headers or {}
        run_id = run_id or self.current_run_id
        self.conn.execute(
            """
            INSERT INTO raw_api_response (
              id, run_id, ts, source, source_type, account_seq, channel, endpoint, http_method,
              request_id, request_hash, response_hash, status_code, rate_limit_limit,
              rate_limit_remaining, rate_limit_reset, body_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_id,
                run_id,
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
        run_id: str | None = None,
    ) -> str:
        now = ts or utc_now()
        run_id = run_id or self.current_run_id
        if source_status == "ok" and last_success_at is None:
            last_success_at = now
        health_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO source_health_snapshot (
              id, run_id, ts, source, channel, last_success_at, max_age_ms,
              heartbeat_timeout_ms, lag_ms, dropped_events, source_status,
              action, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                health_id,
                run_id,
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

    def ingest_accounts(
        self,
        body: Any,
        *,
        raw_ref: str,
        ts: str | None = None,
        run_id: str | None = None,
    ) -> int:
        now = ts or utc_now()
        run_id = run_id or self.current_run_id
        accounts = require_accounts(body)
        rows = []
        for item in accounts:
            account_seq = str(_get(item, "accountSeq", "account_seq", "seq", default="")).strip()
            if not account_seq:
                continue
            rows.append(
                (
                    str(uuid.uuid4()),
                    run_id,
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
              id, run_id, ts, account_seq, account_no_masked, account_type, broker, raw_response_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        run_id: str | None = None,
    ) -> int:
        now = ts or utc_now()
        run_id = run_id or self.current_run_id
        holdings = holdings_items(body)
        rows = []
        for item in holdings:
            symbol = str(_get(item, "symbol", "ticker", "stockCode", default="")).strip().upper()
            quantity_decimal = _decimal_text(_get(item, "quantity", "qty", "holdingQuantity"))
            average_purchase_price_decimal = _decimal_text(
                _get(item, "averagePurchasePrice", "avgPrice", "averagePrice")
            )
            last_price_decimal = _decimal_text(_get(item, "lastPrice", "currentPrice", "price"))
            market_value = item["marketValue"]
            profit_loss = item["profitLoss"]
            cost = item["cost"]
            market_value_decimal = _decimal_text(market_value.get("amount"))
            profit_loss_decimal = _decimal_text(profit_loss.get("amount"))
            cost_decimal = _sum_decimal_text(cost.get("commission"), cost.get("tax"))
            if (
                not symbol
                or quantity_decimal is None
                or average_purchase_price_decimal is None
                or last_price_decimal is None
                or market_value_decimal is None
                or profit_loss_decimal is None
            ):
                raise ValueError("holding item has invalid decimal field")
            rows.append(
                (
                    str(uuid.uuid4()),
                    run_id,
                    now,
                    account_seq,
                    symbol,
                    float(quantity_decimal),
                    float(average_purchase_price_decimal),
                    float(last_price_decimal),
                    float(market_value_decimal),
                    float(profit_loss_decimal),
                    float(cost_decimal) if cost_decimal is not None else None,
                    _get(item, "currency", default=None),
                    raw_ref,
                    now,
                    quantity_decimal,
                    average_purchase_price_decimal,
                    last_price_decimal,
                    market_value_decimal,
                    profit_loss_decimal,
                    cost_decimal,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO holding_snapshot (
              id, run_id, ts, account_seq, symbol, quantity, average_purchase_price, last_price,
              market_value, profit_loss, cost, currency, raw_response_ref, created_at
              , quantity_decimal, average_purchase_price_decimal, last_price_decimal,
              market_value_decimal, profit_loss_decimal, cost_decimal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        run_id: str | None = None,
    ) -> int:
        now = ts or utc_now()
        run_id = run_id or self.current_run_id
        items = [require_buying_power(body)]
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            decimal_value = _decimal_text(
                _get(item, "cashBuyingPower", "cash_buying_power", "buyingPower")
            )
            currency = str(_get(item, "currency", default="")).upper()
            if decimal_value is None or not currency:
                raise ValueError("buying power has invalid currency or decimal")
            rows.append(
                (
                    str(uuid.uuid4()),
                    run_id,
                    now,
                    account_seq,
                    currency,
                    float(decimal_value),
                    raw_ref,
                    now,
                    decimal_value,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO buying_power_snapshot (
              id, run_id, ts, account_seq, currency, cash_buying_power, raw_response_ref, created_at,
              cash_buying_power_decimal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        run_id: str | None = None,
        status_group: str | None = None,
    ) -> int:
        now = ts or utc_now()
        run_id = run_id or self.current_run_id
        orders = _official_orders(body, status_group=status_group)
        rows = []
        for item in orders:
            broker_order_id = str(_get(item, "orderId", "id", default="")).strip()
            symbol = str(_get(item, "symbol", "ticker", default="")).strip().upper()
            status = str(_get(item, "status", default="UNKNOWN")).strip()
            if not broker_order_id or not symbol:
                continue
            execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
            quantity_decimal = _decimal_text(_get(item, "quantity"))
            order_amount_decimal = _decimal_text(_get(item, "orderAmount", "order_amount"))
            price_decimal = _decimal_text(_get(item, "price"))
            filled_qty_decimal = _decimal_text(_get(execution, "filledQuantity"))
            filled_amount_decimal = _decimal_text(_get(execution, "filledAmount"))
            average_filled_price_decimal = _decimal_text(
                _get(execution, "averageFilledPrice")
            )
            commission_decimal = _decimal_text(_get(execution, "commission"))
            tax_decimal = _decimal_text(_get(execution, "tax"))
            if quantity_decimal is None:
                raise ValueError("order quantity must be a decimal")
            rows.append(
                (
                    str(uuid.uuid4()),
                    run_id,
                    now,
                    account_seq,
                    broker_order_id,
                    _get(item, "clientOrderId", "client_order_id"),
                    symbol,
                    _get(item, "side"),
                    _get(item, "orderType", "order_type"),
                    _get(item, "timeInForce", "time_in_force"),
                    status,
                    float(quantity_decimal),
                    float(order_amount_decimal) if order_amount_decimal is not None else None,
                    float(price_decimal) if price_decimal is not None else None,
                    float(filled_qty_decimal) if filled_qty_decimal is not None else None,
                    float(filled_amount_decimal) if filled_amount_decimal is not None else None,
                    float(average_filled_price_decimal)
                    if average_filled_price_decimal is not None
                    else None,
                    float(commission_decimal) if commission_decimal is not None else None,
                    float(tax_decimal) if tax_decimal is not None else None,
                    _get(execution, "settlementDate"),
                    _get(item, "orderedAt"),
                    _get(item, "canceledAt"),
                    raw_ref,
                    now,
                    _get(item, "currency"),
                    quantity_decimal,
                    order_amount_decimal,
                    price_decimal,
                    filled_qty_decimal,
                    filled_amount_decimal,
                    average_filled_price_decimal,
                    commission_decimal,
                    tax_decimal,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO broker_order_snapshot (
              id, run_id, ts, account_seq, broker_order_id, client_order_id, symbol, side,
              order_type, time_in_force, status, quantity, order_amount, price,
              cumulative_filled_qty, cumulative_filled_amount, average_filled_price,
              cumulative_commission, cumulative_tax, settlement_date, ordered_at,
              canceled_at, raw_response_ref, created_at, currency, quantity_decimal,
              order_amount_decimal, price_decimal, cumulative_filled_qty_decimal,
              cumulative_filled_amount_decimal, average_filled_price_decimal,
              cumulative_commission_decimal, cumulative_tax_decimal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        run_id: str | None = None,
        status_group: str | None = None,
    ) -> tuple[int, int]:
        now = ts or utc_now()
        run_id = run_id or self.current_run_id
        orders = _official_orders(body, status_group=status_group)
        inserted_snapshots = 0
        inserted_deltas = 0
        for item in orders:
            broker_order_id = str(_get(item, "orderId", "id", default="")).strip()
            if not broker_order_id:
                continue
            execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
            filled_qty_decimal = _decimal_text(_get(execution, "filledQuantity"))
            filled_amount_decimal = _decimal_text(_get(execution, "filledAmount"))
            if filled_qty_decimal is None or filled_amount_decimal is None:
                continue
            filled_qty = float(filled_qty_decimal)
            filled_amount = float(filled_amount_decimal)
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
            commission_decimal = _decimal_text(_get(execution, "commission")) or "0"
            tax_decimal = _decimal_text(_get(execution, "tax")) or "0"
            cumulative_commission = float(commission_decimal)
            cumulative_tax = float(tax_decimal)
            order_status = str(_get(item, "status", default="UNKNOWN"))
            average_filled_price_decimal = _decimal_text(
                _get(execution, "averageFilledPrice")
            )
            average_filled_price = (
                float(average_filled_price_decimal)
                if average_filled_price_decimal is not None
                else None
            )
            settlement_date = _get(execution, "settlementDate")

            if previous is not None:
                unchanged = (
                    float(previous["cumulative_filled_qty"]) == filled_qty
                    and float(previous["cumulative_filled_amount"]) == filled_amount
                    and _same_optional_float(
                        previous["average_filled_price"],
                        average_filled_price,
                    )
                    and float(previous["cumulative_commission"] or 0) == cumulative_commission
                    and float(previous["cumulative_tax"] or 0) == cumulative_tax
                    and previous["order_status"] == order_status
                    and previous["settlement_date"] == settlement_date
                )
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
                  id, run_id, ts, account_seq, order_id, broker_order_id, snapshot_seq,
                  order_status, cumulative_filled_qty, cumulative_filled_amount,
                  average_filled_price, cumulative_commission, cumulative_tax,
                  settlement_date, raw_snapshot_ref, created_at, currency,
                  cumulative_filled_qty_decimal, cumulative_filled_amount_decimal,
                  average_filled_price_decimal, cumulative_commission_decimal,
                  cumulative_tax_decimal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    run_id,
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
                    _get(item, "currency"),
                    filled_qty_decimal,
                    filled_amount_decimal,
                    average_filled_price_decimal,
                    commission_decimal,
                    tax_decimal,
                ),
            )
            inserted_snapshots += 1
            if delta_qty or delta_amount or delta_commission or delta_tax:
                self.conn.execute(
                    """
                    INSERT INTO execution_delta_log (
                      id, run_id, ts, account_seq, order_id, broker_order_id, from_snapshot_id,
                      to_snapshot_id, delta_filled_qty, delta_filled_amount,
                      delta_commission, delta_tax, created_at, currency,
                      delta_filled_qty_decimal, delta_filled_amount_decimal,
                      delta_commission_decimal, delta_tax_decimal
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
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
                        _get(item, "currency"),
                        format(Decimal(str(delta_qty)), "f"),
                        format(Decimal(str(delta_amount)), "f"),
                        format(Decimal(str(delta_commission)), "f"),
                        format(Decimal(str(delta_tax)), "f"),
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
        run_id: str | None = None,
    ) -> int:
        now = ts or utc_now()
        run_id = run_id or self.current_run_id
        if not fallback_symbol:
            raise ValueError("sellable quantity requires the requested symbol")
        items = [require_sellable_quantity(body)]
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = fallback_symbol.strip().upper()
            quantity_decimal = _decimal_text(
                _get(item, "sellableQuantity", "sellable_quantity", "quantity")
            )
            if not symbol or quantity_decimal is None:
                raise ValueError("sellable quantity has invalid symbol or decimal")
            rows.append(
                (
                    str(uuid.uuid4()),
                    run_id,
                    now,
                    account_seq,
                    symbol,
                    float(quantity_decimal),
                    raw_ref,
                    now,
                    quantity_decimal,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO sellable_quantity_snapshot (
              id, run_id, ts, account_seq, symbol, sellable_quantity, raw_response_ref, created_at,
              sellable_quantity_decimal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        run_id: str | None = None,
    ) -> int:
        now = ts or utc_now()
        run_id = run_id or self.current_run_id
        items = commission_rate_items(body)
        rows = []
        for item in items:
            rate_decimal = _decimal_text(item.get("commissionRate"))
            if rate_decimal is None:
                raise ValueError("commission rate is not a decimal")
            rows.append(
                (
                    str(uuid.uuid4()),
                    run_id,
                    now,
                    account_seq,
                    str(item["marketCountry"]),
                    rate_decimal,
                    item.get("startDate"),
                    item.get("endDate"),
                    raw_ref,
                    now,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO commission_rate_schedule_snapshot (
              id, run_id, ts, account_seq, market_country, commission_rate_decimal,
              start_date, end_date, raw_response_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def latest_complete_run(self, account_seq: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM snapshot_run
            WHERE account_seq = ? AND status = 'COMPLETE'
            ORDER BY completed_at DESC
            LIMIT 1
            """,
            (account_seq,),
        ).fetchone()

    def explain_account_state(
        self,
        account_seq: str,
        *,
        run_id: str | None = None,
    ) -> AccountStateExplanation:
        run = (
            self.conn.execute(
                "SELECT * FROM snapshot_run WHERE run_id = ? AND account_seq = ?",
                (run_id, account_seq),
            ).fetchone()
            if run_id
            else self.latest_complete_run(account_seq)
        )
        blockers: list[str] = []
        if run is None or run["status"] != "COMPLETE":
            blockers.append("missing_complete_snapshot_run")
            resolved_run_id = None
            as_of = utc_now()
            holdings: list[sqlite3.Row] = []
            buying_power_rows: list[sqlite3.Row] = []
            open_orders = 0
        else:
            resolved_run_id = str(run["run_id"])
            as_of = str(run["completed_at"])
            required_endpoints = (
                "/api/v1/accounts",
                "/api/v1/holdings",
                "/api/v1/orders?status=OPEN",
                "/api/v1/buying-power",
                "/api/v1/commissions",
            )
            for endpoint in required_endpoints:
                row = self.conn.execute(
                    """
                    SELECT 1
                    FROM raw_api_response
                    WHERE run_id = ? AND endpoint LIKE ?
                      AND (endpoint = '/api/v1/accounts' OR account_seq = ?)
                      AND status_code BETWEEN 200 AND 299
                    LIMIT 1
                    """,
                    (resolved_run_id, f"{endpoint}%", account_seq),
                ).fetchone()
                if row is None:
                    blockers.append(f"missing_successful_endpoint:{endpoint}")
            holdings = self.conn.execute(
                """
                SELECT symbol, currency, market_value_decimal, ts
                FROM holding_snapshot
                WHERE run_id = ? AND account_seq = ?
                ORDER BY symbol
                """,
                (resolved_run_id, account_seq),
            ).fetchall()
            buying_power_rows = self.conn.execute(
                """
                SELECT currency, cash_buying_power_decimal
                FROM buying_power_snapshot
                WHERE run_id = ? AND account_seq = ?
                ORDER BY currency
                """,
                (resolved_run_id, account_seq),
            ).fetchall()
            if not buying_power_rows:
                blockers.append("missing_normalized_buying_power")
            open_orders = int(
                self.conn.execute(
                    """
                    SELECT COUNT(DISTINCT broker_order_id)
                    FROM broker_order_snapshot
                    WHERE run_id = ? AND account_seq = ?
                      AND status IN ('PENDING', 'PENDING_CANCEL', 'PENDING_REPLACE', 'PARTIAL_FILLED')
                    """,
                    (resolved_run_id, account_seq),
                ).fetchone()[0]
            )

        market_value_by_currency: dict[str, Decimal] = {}
        for row in holdings:
            amount = _decimal_text(row["market_value_decimal"])
            currency = str(row["currency"] or "")
            if amount is None or not currency:
                blockers.append("invalid_normalized_holding_value")
                continue
            market_value_by_currency[currency] = market_value_by_currency.get(
                currency, Decimal("0")
            ) + Decimal(amount)
        market_value = {
            currency: format(amount, "f") for currency, amount in market_value_by_currency.items()
        }
        buying_power = {
            str(row["currency"]): str(row["cash_buying_power_decimal"])
            for row in buying_power_rows
            if row["cash_buying_power_decimal"] is not None
        }
        lines = [
            f"account_seq={account_seq}",
            f"snapshot_run_id={resolved_run_id or 'none'}",
            f"as_of={as_of}",
            f"holdings_count={len(holdings)}",
            f"holding_market_value_by_currency={market_value}",
            f"open_orders_count={open_orders}",
            f"buying_power_by_currency={buying_power}",
            f"blockers={blockers or ['none']}",
        ]
        return AccountStateExplanation(
            account_seq=account_seq,
            as_of=as_of,
            run_id=resolved_run_id,
            holdings_count=len(holdings),
            open_orders_count=open_orders,
            market_value_by_currency=market_value,
            buying_power_by_currency=buying_power,
            blockers=blockers,
            lines=lines,
        )
