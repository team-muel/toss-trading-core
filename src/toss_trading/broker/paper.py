from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .base import BrokerCapabilities


def _decimal(value: object, *, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _text(value: Decimal) -> str:
    return format(value, "f")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperBrokerAdapter:
    """Persistent, explicitly-filled broker simulator; never sends a live order."""

    capabilities = BrokerCapabilities(
        market_data=False,
        order_entry=True,
        order_cancel=True,
        fills=True,
        balances=True,
        conditional_order_entry=False,
        conditional_order_modify=False,
        options_trading=False,
        margin_status=False,
    )

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        initial_cash: dict[str, str] | None = None,
        commission_bps: str = "10",
        slippage_bps: str = "2.0",
    ) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.commission_bps = _decimal(commission_bps, name="commission_bps")
        self.slippage_bps = _decimal(slippage_bps, name="slippage_bps")
        if min(self.commission_bps, self.slippage_bps) < 0:
            raise ValueError("paper costs must be nonnegative")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_cash (
              currency TEXT PRIMARY KEY,
              balance_decimal TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_position (
              symbol TEXT NOT NULL,
              currency TEXT NOT NULL,
              quantity_decimal TEXT NOT NULL,
              average_cost_decimal TEXT NOT NULL,
              PRIMARY KEY (symbol, currency)
            );
            CREATE TABLE IF NOT EXISTS paper_order (
              client_order_id TEXT PRIMARY KEY,
              payload_hash TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              symbol TEXT NOT NULL,
              currency TEXT NOT NULL,
              side TEXT NOT NULL,
              order_type TEXT NOT NULL DEFAULT 'MARKET',
              limit_price_decimal TEXT,
              quantity_decimal TEXT,
              order_amount_decimal TEXT,
              filled_quantity_decimal TEXT NOT NULL DEFAULT '0',
              filled_amount_decimal TEXT NOT NULL DEFAULT '0',
              commission_decimal TEXT NOT NULL DEFAULT '0',
              average_filled_price_decimal TEXT,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_fill (
              id TEXT PRIMARY KEY,
              client_order_id TEXT NOT NULL,
              ts TEXT NOT NULL,
              quantity_decimal TEXT NOT NULL,
              amount_decimal TEXT NOT NULL,
              price_decimal TEXT NOT NULL,
              commission_decimal TEXT NOT NULL,
              settlement_date TEXT,
              FOREIGN KEY (client_order_id) REFERENCES paper_order(client_order_id)
            );
            """
        )
        order_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(paper_order)").fetchall()
        }
        if "order_type" not in order_columns:
            self.conn.execute(
                "ALTER TABLE paper_order "
                "ADD COLUMN order_type TEXT NOT NULL DEFAULT 'MARKET'"
            )
        if "limit_price_decimal" not in order_columns:
            self.conn.execute(
                "ALTER TABLE paper_order ADD COLUMN limit_price_decimal TEXT"
            )
        for currency, balance in (initial_cash or {}).items():
            amount = _decimal(balance, name="initial cash")
            self.conn.execute(
                """
                INSERT OR IGNORE INTO paper_cash(currency, balance_decimal)
                VALUES (?, ?)
                """,
                (currency.strip().upper(), _text(amount)),
            )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def submit_order(self, order: dict) -> dict:
        client_order_id = str(order.get("client_order_id") or "").strip()
        symbol = str(order.get("symbol") or "").strip().upper()
        currency = str(order.get("currency") or "USD").strip().upper()
        side = str(order.get("side") or "").strip().upper()
        if not client_order_id or not symbol or not currency:
            raise ValueError("paper order requires client_order_id, symbol, and currency")
        if side not in {"BUY", "SELL"}:
            raise ValueError("paper order side must be BUY or SELL")
        limit_price = order.get("limit_price", order.get("price"))
        order_type = str(
            order.get("order_type")
            or order.get("orderType")
            or ("LIMIT" if limit_price is not None else "MARKET")
        ).strip().upper()
        if order_type not in {"MARKET", "LIMIT"}:
            raise ValueError("paper order_type must be MARKET or LIMIT")
        if order_type == "LIMIT" and limit_price is None:
            raise ValueError("LIMIT paper order requires limit_price or price")
        if order_type == "MARKET" and limit_price is not None:
            raise ValueError("MARKET paper order must not include a limit price")
        limit_price_decimal = (
            _decimal(limit_price, name="limit_price")
            if limit_price is not None
            else None
        )
        if limit_price_decimal is not None and limit_price_decimal <= 0:
            raise ValueError("limit_price must be positive")
        quantity = order.get("qty")
        amount = order.get("order_amount")
        if (quantity is None) == (amount is None):
            raise ValueError("paper order requires exactly one of qty or order_amount")
        quantity_decimal = (
            _decimal(quantity, name="qty") if quantity is not None else None
        )
        amount_decimal = (
            _decimal(amount, name="order_amount") if amount is not None else None
        )
        if quantity_decimal is not None and quantity_decimal <= 0:
            raise ValueError("qty must be positive")
        if amount_decimal is not None and amount_decimal <= 0:
            raise ValueError("order_amount must be positive")
        payload = json.dumps(
            order,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO paper_order (
                  client_order_id, payload_hash, payload_json, symbol, currency,
                  side, order_type, limit_price_decimal, quantity_decimal,
                  order_amount_decimal, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'paper_submitted', ?, ?)
                ON CONFLICT(client_order_id) DO NOTHING
                """,
                (
                    client_order_id,
                    payload_hash,
                    payload,
                    symbol,
                    currency,
                    side,
                    order_type,
                    (
                        _text(limit_price_decimal)
                        if limit_price_decimal is not None
                        else None
                    ),
                    (
                        _text(quantity_decimal)
                        if quantity_decimal is not None
                        else None
                    ),
                    _text(amount_decimal) if amount_decimal is not None else None,
                    now,
                    now,
                ),
            )
            existing = self.conn.execute(
                "SELECT * FROM paper_order WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if existing["payload_hash"] != payload_hash:
                raise ValueError(
                    "client_order_id conflicts with a different paper order payload"
                )
        return self._order_result(existing)

    def process_order(
        self,
        client_order_id: str,
        *,
        market_price: str,
        fill_ratio: str = "1",
        settlement_date: str | None = None,
    ) -> dict:
        row = self.conn.execute(
            "SELECT * FROM paper_order WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
        if row is None:
            raise ValueError("paper order not found")
        if row["status"] in {"paper_filled", "paper_cancelled"}:
            return self._order_result(row)
        market = _decimal(market_price, name="market_price")
        ratio = _decimal(fill_ratio, name="fill_ratio")
        if market <= 0 or not Decimal("0") < ratio <= Decimal("1"):
            raise ValueError("market_price and fill_ratio must be positive")
        side = str(row["side"])
        limit_price = (
            Decimal(row["limit_price_decimal"])
            if row["limit_price_decimal"] is not None
            else None
        )
        if row["order_type"] == "LIMIT":
            marketable = (
                market <= limit_price if side == "BUY" else market >= limit_price
            )
            if not marketable:
                return self._order_result(row)
        slip = self.slippage_bps / Decimal("10000")
        fill_price = market * (
            Decimal("1") + slip if side == "BUY" else Decimal("1") - slip
        )
        if limit_price is not None:
            fill_price = (
                min(fill_price, limit_price)
                if side == "BUY"
                else max(fill_price, limit_price)
            )
        already_qty = Decimal(row["filled_quantity_decimal"])
        already_amount = Decimal(row["filled_amount_decimal"])
        if row["quantity_decimal"] is not None:
            remaining_qty = Decimal(row["quantity_decimal"]) - already_qty
            fill_qty = remaining_qty * ratio
            fill_amount = fill_qty * fill_price
            complete = fill_qty == remaining_qty
        else:
            remaining_amount = Decimal(row["order_amount_decimal"]) - already_amount
            fill_amount = remaining_amount * ratio
            fill_qty = fill_amount / fill_price
            complete = fill_amount == remaining_amount
        if min(fill_qty, fill_amount) <= 0:
            raise ValueError("paper order has no remaining quantity")
        commission = fill_amount * self.commission_bps / Decimal("10000")

        cash_row = self.conn.execute(
            "SELECT balance_decimal FROM paper_cash WHERE currency = ?",
            (row["currency"],),
        ).fetchone()
        cash = Decimal(cash_row["balance_decimal"]) if cash_row else Decimal("0")
        position = self.conn.execute(
            """
            SELECT quantity_decimal, average_cost_decimal
            FROM paper_position
            WHERE symbol = ? AND currency = ?
            """,
            (row["symbol"], row["currency"]),
        ).fetchone()
        position_qty = (
            Decimal(position["quantity_decimal"]) if position else Decimal("0")
        )
        average_cost = (
            Decimal(position["average_cost_decimal"]) if position else Decimal("0")
        )
        if side == "BUY":
            cash_change = -(fill_amount + commission)
            if cash + cash_change < 0:
                raise ValueError("paper cash is insufficient")
            new_position_qty = position_qty + fill_qty
            new_average_cost = (
                position_qty * average_cost + fill_amount + commission
            ) / new_position_qty
        else:
            if position_qty < fill_qty:
                raise ValueError("paper position is insufficient")
            cash_change = fill_amount - commission
            new_position_qty = position_qty - fill_qty
            new_average_cost = average_cost if new_position_qty else Decimal("0")

        new_filled_qty = already_qty + fill_qty
        new_filled_amount = already_amount + fill_amount
        new_commission = Decimal(row["commission_decimal"]) + commission
        average_fill = new_filled_amount / new_filled_qty
        now = _utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO paper_cash(currency, balance_decimal)
                VALUES (?, ?)
                ON CONFLICT(currency) DO UPDATE SET
                  balance_decimal = excluded.balance_decimal
                """,
                (row["currency"], _text(cash + cash_change)),
            )
            self.conn.execute(
                """
                INSERT INTO paper_position(
                  symbol, currency, quantity_decimal, average_cost_decimal
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, currency) DO UPDATE SET
                  quantity_decimal = excluded.quantity_decimal,
                  average_cost_decimal = excluded.average_cost_decimal
                """,
                (
                    row["symbol"],
                    row["currency"],
                    _text(new_position_qty),
                    _text(new_average_cost),
                ),
            )
            self.conn.execute(
                """
                UPDATE paper_order
                SET filled_quantity_decimal = ?,
                    filled_amount_decimal = ?,
                    commission_decimal = ?,
                    average_filled_price_decimal = ?,
                    status = ?,
                    updated_at = ?
                WHERE client_order_id = ?
                """,
                (
                    _text(new_filled_qty),
                    _text(new_filled_amount),
                    _text(new_commission),
                    _text(average_fill),
                    "paper_filled" if complete else "paper_partial_filled",
                    now,
                    client_order_id,
                ),
            )
            self.conn.execute(
                """
                INSERT INTO paper_fill (
                  id, client_order_id, ts, quantity_decimal, amount_decimal,
                  price_decimal, commission_decimal, settlement_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    client_order_id,
                    now,
                    _text(fill_qty),
                    _text(fill_amount),
                    _text(fill_price),
                    _text(commission),
                    settlement_date,
                ),
            )
        return self._order_result(
            self.conn.execute(
                "SELECT * FROM paper_order WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        )

    def cancel_order(self, client_order_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM paper_order WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
        if row is None:
            return {"client_order_id": client_order_id, "status": "not_found"}
        if row["status"] not in {"paper_filled", "paper_cancelled"}:
            self.conn.execute(
                """
                UPDATE paper_order
                SET status = 'paper_cancelled', updated_at = ?
                WHERE client_order_id = ?
                """,
                (_utc_now(), client_order_id),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM paper_order WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return self._order_result(row)

    def get_balances(self) -> dict:
        rows = self.conn.execute(
            "SELECT currency, balance_decimal FROM paper_cash ORDER BY currency"
        ).fetchall()
        return {
            "mode": "paper",
            "cash": {row["currency"]: row["balance_decimal"] for row in rows},
            "margin_used": None,
        }

    def get_positions(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT symbol, currency, quantity_decimal, average_cost_decimal
            FROM paper_position
            WHERE CAST(quantity_decimal AS NUMERIC) != 0
            ORDER BY symbol, currency
            """
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _order_result(row: sqlite3.Row) -> dict:
        return {
            "client_order_id": row["client_order_id"],
            "symbol": row["symbol"],
            "currency": row["currency"],
            "side": row["side"],
            "order_type": row["order_type"],
            "limit_price": row["limit_price_decimal"],
            "qty": row["quantity_decimal"],
            "order_amount": row["order_amount_decimal"],
            "filled_qty": row["filled_quantity_decimal"],
            "filled_amount": row["filled_amount_decimal"],
            "commission": row["commission_decimal"],
            "average_filled_price": row["average_filled_price_decimal"],
            "status": row["status"],
        }
