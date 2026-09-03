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
from toss_trading.resources import resolve_resource
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


def _stored_decimal(row: sqlite3.Row, decimal_key: str, numeric_key: str) -> Decimal:
    value = row[decimal_key] if row[decimal_key] is not None else row[numeric_key]
    return Decimal(str(value))


def _same_optional_decimal(left: Any, right: str | None) -> bool:
    left_text = _decimal_text(left)
    if left_text is None or right is None:
        return left_text is None and right is None
    return Decimal(left_text) == Decimal(right)


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


@dataclass(frozen=True)
class ReservedCashResult:
    amount_by_currency: dict[str, str]
    blockers: list[str]


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
        sql = resolve_resource(schema_path).read_text(encoding="utf-8")
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
            version = 1
        if version < 2:
            existing = {
                row["name"]
                for row in self.conn.execute("PRAGMA table_info(cash_ledger)").fetchall()
            }
            if "amount_decimal" not in existing:
                self.conn.execute("ALTER TABLE cash_ledger ADD COLUMN amount_decimal TEXT")
                self.conn.execute(
                    """
                    UPDATE cash_ledger
                    SET amount_decimal = CAST(amount AS TEXT)
                    WHERE amount_decimal IS NULL
                    """
                )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cash_ledger_account_currency_settlement
                ON cash_ledger(account_seq, currency, settlement_date, ts)
                """
            )
            self.conn.execute("PRAGMA user_version = 2")
            version = 2
        if version < 3:
            reconciliation_columns = {
                row["name"]
                for row in self.conn.execute(
                    "PRAGMA table_info(broker_reconciliation_log)"
                ).fetchall()
            }
            for column in ("resolved_at TEXT", "resolution_note TEXT"):
                name = column.split()[0]
                if name not in reconciliation_columns:
                    self.conn.execute(
                        f"ALTER TABLE broker_reconciliation_log ADD COLUMN {column}"
                    )
            self.conn.executescript(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_ledger_source_event
                  ON cash_ledger(source_ref, event_type)
                  WHERE source_ref IS NOT NULL;
                CREATE TABLE IF NOT EXISTS cash_ledger_genesis (
                  account_seq TEXT NOT NULL,
                  currency TEXT NOT NULL,
                  as_of TEXT NOT NULL,
                  opening_balance REAL NOT NULL,
                  opening_balance_decimal TEXT NOT NULL,
                  evidence_ref TEXT NOT NULL,
                  approved_by TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY (account_seq, currency)
                );
                CREATE INDEX IF NOT EXISTS idx_reconciliation_unresolved
                  ON broker_reconciliation_log(account_seq, status, resolved_at, ts DESC);
                """
            )
            self.conn.execute("PRAGMA user_version = 3")
            version = 3
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
            ) VALUES (?, ?, ?, ?, 'RUNNING', 1, 'toss-openapi-1.2.14', ?, ?, ?)
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
              symbol_id, toss_symbol, ticker, asset_class, currency, timezone,
              mic, effective_from, effective_to, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.symbol_id,
                    item.toss_symbol,
                    item.ticker,
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
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO client_order_id_registry (
                  client_order_id, account_seq, first_used_at,
                  reuse_forbidden, request_hash
                ) VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(client_order_id) DO NOTHING
                """,
                (client_order_id, account_seq, utc_now(), request_hash),
            )
            if cursor.rowcount == 1:
                return False
            row = self.conn.execute(
                """
                SELECT account_seq, request_hash
                FROM client_order_id_registry
                WHERE client_order_id = ?
                """,
                (client_order_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("client_order_id reservation was lost")
            if row["account_seq"] != account_seq:
                raise ValueError(
                    "client_order_id is already reserved for a different account"
                )
            if row["request_hash"] == request_hash:
                return True
            raise ValueError(
                "client_order_id is already reserved for a different payload"
            )

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
            if quantity_decimal is None and order_amount_decimal is None:
                raise ValueError(
                    "order must contain a decimal quantity or order amount"
                )
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
                    float(quantity_decimal) if quantity_decimal is not None else None,
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
            filled_qty_value = Decimal(filled_qty_decimal)
            filled_amount_value = Decimal(filled_amount_decimal)
            filled_qty = float(filled_qty_value)
            filled_amount = float(filled_amount_value)
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
            cumulative_commission_value = Decimal(commission_decimal)
            cumulative_tax_value = Decimal(tax_decimal)
            cumulative_commission = float(cumulative_commission_value)
            cumulative_tax = float(cumulative_tax_value)
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
                previous_qty = _stored_decimal(
                    previous,
                    "cumulative_filled_qty_decimal",
                    "cumulative_filled_qty",
                )
                previous_amount = _stored_decimal(
                    previous,
                    "cumulative_filled_amount_decimal",
                    "cumulative_filled_amount",
                )
                previous_commission = _stored_decimal(
                    previous,
                    "cumulative_commission_decimal",
                    "cumulative_commission",
                )
                previous_tax = _stored_decimal(
                    previous,
                    "cumulative_tax_decimal",
                    "cumulative_tax",
                )
                unchanged = (
                    previous_qty == filled_qty_value
                    and previous_amount == filled_amount_value
                    and _same_optional_decimal(
                        previous["average_filled_price_decimal"]
                        if previous["average_filled_price_decimal"] is not None
                        else previous["average_filled_price"],
                        average_filled_price_decimal,
                    )
                    and previous_commission == cumulative_commission_value
                    and previous_tax == cumulative_tax_value
                    and previous["order_status"] == order_status
                    and previous["settlement_date"] == settlement_date
                )
                if unchanged and previous["run_id"] == run_id:
                    continue
                delta_qty = filled_qty_value - previous_qty
                delta_amount = filled_amount_value - previous_amount
                delta_commission = cumulative_commission_value - previous_commission
                delta_tax = cumulative_tax_value - previous_tax
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
                delta_qty = filled_qty_value
                delta_amount = filled_amount_value
                delta_commission = cumulative_commission_value
                delta_tax = cumulative_tax_value
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
                        float(delta_qty),
                        float(delta_amount),
                        float(delta_commission),
                        float(delta_tax),
                        now,
                        _get(item, "currency"),
                        format(delta_qty, "f"),
                        format(delta_amount, "f"),
                        format(delta_commission, "f"),
                        format(delta_tax, "f"),
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

    def post_execution_cash_events(
        self,
        *,
        account_seq: str,
        run_id: str | None = None,
    ) -> int:
        """Post exact, idempotent cash events from execution deltas.

        This derives only trade principal, commission, and tax movements. It
        does not invent an opening cash balance or treat broker buying power as
        cash. Settlement-aware available cash therefore remains blocked until
        an independently reconciled opening balance exists.
        """

        params: list[Any] = [account_seq]
        run_filter = ""
        if run_id is not None:
            run_filter = "AND d.run_id = ?"
            params.append(run_id)
        deltas = self.conn.execute(
            f"""
            SELECT d.*
            FROM execution_delta_log AS d
            WHERE d.account_seq = ?
              {run_filter}
            ORDER BY d.ts, d.created_at, d.id
            """,
            tuple(params),
        ).fetchall()

        rows: list[tuple[Any, ...]] = []
        for delta in deltas:
            order = self.conn.execute(
                """
                SELECT side, settlement_date, currency
                FROM broker_order_snapshot
                WHERE account_seq = ? AND broker_order_id = ?
                ORDER BY
                  CASE WHEN run_id = ? THEN 0 ELSE 1 END,
                  created_at DESC,
                  ts DESC
                LIMIT 1
                """,
                (account_seq, delta["broker_order_id"], delta["run_id"]),
            ).fetchone()
            if order is None:
                raise ValueError(
                    f"execution delta has no matching broker order: {delta['broker_order_id']}"
                )
            side = str(order["side"] or "").upper()
            if side not in {"BUY", "SELL"}:
                raise ValueError(
                    f"execution delta has no supported order side: {delta['broker_order_id']}"
                )
            currency = str(delta["currency"] or order["currency"] or "").upper()
            if not currency:
                raise ValueError(
                    f"execution delta has no currency: {delta['broker_order_id']}"
                )

            principal = Decimal(str(delta["delta_filled_amount_decimal"]))
            commission = Decimal(str(delta["delta_commission_decimal"] or "0"))
            tax = Decimal(str(delta["delta_tax_decimal"] or "0"))
            if min(principal, commission, tax) < 0:
                raise ValueError(
                    f"execution delta contains a negative cash component: {delta['id']}"
                )

            events = [
                (
                    "TRADE_COST" if side == "BUY" else "TRADE_PROCEEDS",
                    -principal if side == "BUY" else principal,
                    0,
                ),
                ("COMMISSION_FEE", -commission, 0),
                ("REGULATORY_FEE", -tax, 1),
            ]
            for event_type, amount, tax_relevant in events:
                if amount == 0:
                    continue
                amount_decimal = format(amount, "f")
                event_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"toss-cash-ledger:{delta['id']}:{event_type}",
                    )
                )
                rows.append(
                    (
                        event_id,
                        delta["ts"],
                        account_seq,
                        currency,
                        event_type,
                        float(amount),
                        amount_decimal,
                        order["settlement_date"],
                        delta["id"],
                        tax_relevant,
                        utc_now(),
                    )
                )

        changes_before = self.conn.total_changes
        self.conn.executemany(
            """
            INSERT INTO cash_ledger (
              id, ts, account_seq, currency, event_type, amount, amount_decimal,
              settlement_date, source_ref, tax_relevant, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              settlement_date = excluded.settlement_date
            WHERE cash_ledger.settlement_date IS NOT excluded.settlement_date
            """,
            rows,
        )
        self.conn.commit()
        return self.conn.total_changes - changes_before

    def cash_event_gaps(self, *, account_seq: str) -> list[str]:
        """Return exact missing or inconsistent cash events for every execution delta."""

        gaps: list[str] = []
        deltas = self.conn.execute(
            """
            SELECT *
            FROM execution_delta_log
            WHERE account_seq = ?
            ORDER BY ts, created_at, id
            """,
            (account_seq,),
        ).fetchall()
        for delta in deltas:
            order = self.conn.execute(
                """
                SELECT side, settlement_date, currency
                FROM broker_order_snapshot
                WHERE account_seq = ? AND broker_order_id = ?
                ORDER BY
                  CASE WHEN run_id = ? THEN 0 ELSE 1 END,
                  created_at DESC,
                  ts DESC
                LIMIT 1
                """,
                (account_seq, delta["broker_order_id"], delta["run_id"]),
            ).fetchone()
            if order is None:
                gaps.append(f"execution_cash_missing_order:{delta['id']}")
                continue
            side = str(order["side"] or "").upper()
            if side not in {"BUY", "SELL"}:
                gaps.append(f"execution_cash_invalid_side:{delta['id']}")
                continue
            expected: dict[str, Decimal] = {}
            principal = Decimal(str(delta["delta_filled_amount_decimal"]))
            commission = Decimal(str(delta["delta_commission_decimal"] or "0"))
            tax = Decimal(str(delta["delta_tax_decimal"] or "0"))
            if principal:
                expected[
                    "TRADE_COST" if side == "BUY" else "TRADE_PROCEEDS"
                ] = -principal if side == "BUY" else principal
            if commission:
                expected["COMMISSION_FEE"] = -commission
            if tax:
                expected["REGULATORY_FEE"] = -tax

            actual_rows = self.conn.execute(
                """
                SELECT event_type, amount_decimal, settlement_date
                FROM cash_ledger
                WHERE source_ref = ?
                """,
                (delta["id"],),
            ).fetchall()
            actual = {str(row["event_type"]): row for row in actual_rows}
            if len(actual) != len(actual_rows):
                gaps.append(f"execution_cash_duplicate_event:{delta['id']}")
            for event_type, amount in expected.items():
                row = actual.get(event_type)
                if row is None:
                    gaps.append(
                        f"execution_cash_event_missing:{delta['id']}:{event_type}"
                    )
                    continue
                if Decimal(str(row["amount_decimal"])) != amount:
                    gaps.append(
                        f"execution_cash_amount_mismatch:{delta['id']}:{event_type}"
                    )
                if row["settlement_date"] != order["settlement_date"]:
                    gaps.append(
                        f"execution_cash_settlement_mismatch:{delta['id']}:{event_type}"
                    )
            unexpected = sorted(set(actual) - set(expected))
            for event_type in unexpected:
                gaps.append(
                    f"execution_cash_unexpected_event:{delta['id']}:{event_type}"
                )
        return gaps

    def record_cash_ledger_genesis(
        self,
        *,
        account_seq: str,
        currency: str,
        as_of: str,
        opening_balance: str,
        evidence_ref: str,
        approved_by: str,
    ) -> None:
        """Record an explicit opening balance without inferring it from buying power."""

        amount_text = _decimal_text(opening_balance)
        if amount_text is None:
            raise ValueError("opening_balance must be a finite decimal")
        if not evidence_ref.strip() or not approved_by.strip():
            raise ValueError("evidence_ref and approved_by are required")
        normalized_currency = currency.strip().upper()
        if not normalized_currency:
            raise ValueError("currency is required")
        values = (
            account_seq,
            normalized_currency,
            as_of,
            float(Decimal(amount_text)),
            amount_text,
            evidence_ref,
            approved_by,
            utc_now(),
        )
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO cash_ledger_genesis (
                  account_seq, currency, as_of, opening_balance,
                  opening_balance_decimal, evidence_ref, approved_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_seq, currency) DO NOTHING
                """,
                values,
            )
            if cursor.rowcount == 1:
                return
            existing = self.conn.execute(
                """
                SELECT as_of, opening_balance_decimal, evidence_ref, approved_by
                FROM cash_ledger_genesis
                WHERE account_seq = ? AND currency = ?
                """,
                (account_seq, normalized_currency),
            ).fetchone()
            if existing is None:
                raise RuntimeError("cash ledger genesis reservation was lost")
            if (
                existing["as_of"] == as_of
                and existing["opening_balance_decimal"] == amount_text
                and existing["evidence_ref"] == evidence_ref
                and existing["approved_by"] == approved_by
            ):
                return
            raise ValueError(
                "cash ledger genesis is immutable; record a separate correction event"
            )

    def resolve_reconciliation_block(self, reconciliation_id: str, *, note: str) -> None:
        if not note.strip():
            raise ValueError("resolution note is required")
        cursor = self.conn.execute(
            """
            UPDATE broker_reconciliation_log
            SET resolved_at = ?, resolution_note = ?
            WHERE id = ? AND status = 'BLOCK' AND resolved_at IS NULL
            """,
            (utc_now(), note.strip(), reconciliation_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("unresolved reconciliation block not found")
        self.conn.commit()

    def reserved_open_buy_cash(
        self,
        *,
        account_seq: str,
        run_id: str,
    ) -> ReservedCashResult:
        """Calculate conservative cash reservations for current OPEN buys."""

        rows = self.conn.execute(
            """
            WITH ranked_orders AS (
              SELECT
                *,
                ROW_NUMBER() OVER (
                  PARTITION BY broker_order_id
                  ORDER BY created_at DESC, ts DESC, id DESC
                ) AS row_rank
              FROM broker_order_snapshot
              WHERE run_id = ? AND account_seq = ?
                AND status IN (
                  'PENDING', 'PENDING_CANCEL', 'PENDING_REPLACE', 'PARTIAL_FILLED'
                )
            )
            SELECT *
            FROM ranked_orders
            WHERE row_rank = 1 AND UPPER(COALESCE(side, '')) = 'BUY'
            ORDER BY broker_order_id
            """,
            (run_id, account_seq),
        ).fetchall()
        totals: dict[str, Decimal] = {}
        blockers: list[str] = []
        for row in rows:
            order_id = str(row["broker_order_id"])
            currency = str(row["currency"] or "").upper()
            if not currency:
                blockers.append(f"open_buy_missing_currency:{order_id}")
                continue
            order_amount_text = _decimal_text(row["order_amount_decimal"])
            filled_amount_text = _decimal_text(row["cumulative_filled_amount_decimal"]) or "0"
            if order_amount_text is not None:
                remaining = Decimal(order_amount_text) - Decimal(filled_amount_text)
            else:
                quantity_text = _decimal_text(row["quantity_decimal"])
                filled_qty_text = _decimal_text(row["cumulative_filled_qty_decimal"]) or "0"
                price_text = _decimal_text(row["price_decimal"])
                if quantity_text is None or price_text is None:
                    blockers.append(f"open_buy_notional_not_resolvable:{order_id}")
                    continue
                remaining_qty = Decimal(quantity_text) - Decimal(filled_qty_text)
                remaining = remaining_qty * Decimal(price_text)
            if remaining < 0:
                blockers.append(f"open_buy_negative_remaining_notional:{order_id}")
                continue
            totals[currency] = totals.get(currency, Decimal("0")) + remaining
        return ReservedCashResult(
            amount_by_currency={
                currency: format(amount, "f")
                for currency, amount in sorted(totals.items())
            },
            blockers=blockers,
        )

    def latest_complete_run(self, account_seq: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM snapshot_run
            WHERE account_seq = ? AND status = 'COMPLETE'
            ORDER BY completed_at DESC, started_at DESC, rowid DESC
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
        for currency in sorted(set(market_value) - set(buying_power)):
            blockers.append(f"missing_buying_power_currency:{currency}")
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
