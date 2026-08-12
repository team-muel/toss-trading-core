from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from toss_trading.account import AccountLedger
from toss_trading.broker import PaperBrokerAdapter
from toss_trading.engines import Signal
from toss_trading.execution import OrderPlanner
from toss_trading.policy import load_policy
from toss_trading.research.costs import ExecutionCostModel
from toss_trading.risk import OrderIntent, RiskHub


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _settlement_date(market_date: str) -> str:
    candidate = date.fromisoformat(market_date) + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def load_latest_raw_prices(
    lake_root: str | Path,
    *,
    through_date: str,
    symbols: set[str],
) -> dict[str, Decimal]:
    """Load one immutable Tiingo raw close per symbol at or before a cutoff."""

    if not symbols:
        return {}
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - dependency gate
        raise RuntimeError("duckdb is required for paper market prices") from exc
    raw_glob = (
        Path(lake_root)
        / "silver"
        / "market_bars"
        / "source=tiingo-eod"
        / "interval=1d"
        / "adjustment=raw"
        / "year=*"
        / "*.parquet"
    )
    files = sorted(raw_glob.parent.parent.glob("year=*/*.parquet"))
    if not files:
        raise ValueError("paper operation found no Tiingo raw Parquet files")
    placeholders = ",".join("?" for _ in symbols)
    query = f"""
        SELECT symbol, close
        FROM read_parquet(?, union_by_name=true, hive_partitioning=true)
        WHERE exchange_local_date <= ?
          AND symbol IN ({placeholders})
          AND quality_flag = 'ok'
        QUALIFY row_number() OVER (
          PARTITION BY symbol
          ORDER BY exchange_local_date DESC, available_at DESC
        ) = 1
    """
    parameters: list[object] = [str(raw_glob), through_date, *sorted(symbols)]
    rows = duckdb.connect().execute(query, parameters).fetchall()
    prices = {str(symbol): Decimal(str(close)) for symbol, close in rows}
    missing = sorted(symbols - set(prices))
    if missing:
        raise ValueError(f"paper operation is missing raw prices: {missing}")
    if any(price <= 0 or not price.is_finite() for price in prices.values()):
        raise ValueError("paper operation received an invalid market price")
    return prices


def _ensure_operation_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS paper_strategy_state (
          strategy TEXT PRIMARY KEY,
          last_rebalance_effective_date TEXT,
          last_artifact_sha256 TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_operation_run (
          run_id TEXT PRIMARY KEY,
          strategy TEXT NOT NULL,
          source_run_id TEXT NOT NULL,
          source_code_revision TEXT NOT NULL,
          market_date TEXT NOT NULL,
          artifact_sha256 TEXT NOT NULL,
          policy_hash TEXT NOT NULL,
          status TEXT NOT NULL,
          evidence_eligible INTEGER NOT NULL,
          report_json TEXT NOT NULL,
          report_sha256 TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def _portfolio_snapshot(
    broker: PaperBrokerAdapter,
    prices: dict[str, Decimal],
) -> dict[str, Any]:
    balances = broker.get_balances()["cash"]
    cash = Decimal(str(balances.get("USD", "0")))
    positions = broker.get_positions()
    market_value = Decimal("0")
    normalized_positions: list[dict[str, str]] = []
    for row in positions:
        symbol = str(row["symbol"])
        if symbol not in prices:
            raise ValueError(f"paper position has no current price: {symbol}")
        quantity = Decimal(str(row["quantity_decimal"]))
        value = quantity * prices[symbol]
        market_value += value
        normalized_positions.append(
            {
                "symbol": symbol,
                "quantity_decimal": _decimal_text(quantity),
                "market_price_decimal": _decimal_text(prices[symbol]),
                "market_value_decimal": _decimal_text(value),
            }
        )
    nav = cash + market_value
    return {
        "cash_decimal": _decimal_text(cash),
        "market_value_decimal": _decimal_text(market_value),
        "nav_decimal": _decimal_text(nav),
        "positions": normalized_positions,
    }


def _latest_rebalance(artifact: dict[str, Any]) -> dict[str, Any]:
    rebalances = artifact.get("rebalances")
    if not isinstance(rebalances, list) or not rebalances:
        raise ValueError("strategy artifact has no published rebalance")
    result = rebalances[-1]
    if not isinstance(result, dict) or not isinstance(
        result.get("target_weights"), dict
    ):
        raise ValueError("strategy artifact has an invalid latest rebalance")
    weights = {
        str(symbol).strip().upper(): Decimal(str(weight))
        for symbol, weight in result["target_weights"].items()
    }
    if not weights or any(
        weight < 0 or not weight.is_finite() for weight in weights.values()
    ):
        raise ValueError("paper target weights must be finite and nonnegative")
    if sum(weights.values(), Decimal("0")) > Decimal("1.000000001"):
        raise ValueError("paper target weights exceed 100 percent")
    return {**result, "target_weights": weights}


def run_paper_operation(
    *,
    artifact: dict[str, Any],
    source_summary: dict[str, Any],
    prices: dict[str, Decimal],
    paper_db_path: str | Path,
    planner_ledger_path: str | Path,
    cost_model: ExecutionCostModel,
    policy_path: str | Path = "config/default_policy.yaml",
) -> dict[str, Any]:
    """Apply a published target once, then persist daily mark-to-market evidence.

    This function can only use the local paper adapter. It never constructs or
    imports a Toss write adapter.
    """

    policy, policy_hash = load_policy(policy_path)
    paper_policy = policy.get("paper_guardrails")
    if not isinstance(paper_policy, dict) or not paper_policy.get("enabled"):
        raise ValueError("paper operation is disabled by policy")
    if paper_policy.get("live_orders_enabled") is not False:
        raise ValueError("paper policy must keep live orders disabled")
    strategy = str(artifact.get("strategy") or "").strip()
    source_run_id = str(source_summary.get("run_id") or "").strip()
    data_progress = source_summary.get("data_progress")
    source_strategy = source_summary.get("strategy")
    if not strategy or not source_run_id:
        raise ValueError("paper operation requires strategy and source run IDs")
    if not isinstance(data_progress, dict) or data_progress.get("state") != "collected":
        raise ValueError("paper operation requires a collected source run")
    if not isinstance(source_strategy, dict):
        raise ValueError("paper operation requires a source strategy summary")
    source_revision = str(source_strategy.get("code_revision") or "")
    if source_revision != str(artifact.get("code_revision") or ""):
        raise ValueError("strategy artifact revision does not match source run")
    market_date = str(data_progress.get("complete_through_date") or "")
    date.fromisoformat(market_date)
    rebalance = _latest_rebalance(artifact)
    target_weights: dict[str, Decimal] = rebalance["target_weights"]
    prospective = artifact.get("prospective_holdout")
    evidence_eligible = bool(
        isinstance(prospective, dict)
        and prospective.get("state") == "completed"
        and prospective.get("metrics_revealed") is True
    )
    artifact_sha = _sha256_json(artifact)
    run_id = "paper-" + _sha256_json(
        {
            "source_run_id": source_run_id,
            "market_date": market_date,
            "artifact_sha256": artifact_sha,
        }
    )[:24]

    paper_db_path = Path(paper_db_path)
    planner_ledger_path = Path(planner_ledger_path)
    paper_db_path.parent.mkdir(parents=True, exist_ok=True)
    planner_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    initial_cash = Decimal(str(paper_policy["initial_cash_usd"]))
    if initial_cash <= 0:
        raise ValueError("paper initial cash must be positive")
    broker = PaperBrokerAdapter(
        paper_db_path,
        initial_cash={"USD": _decimal_text(initial_cash)},
        commission_bps=str(cost_model.commission_bps),
        minimum_commission=str(cost_model.minimum_commission_usd),
        slippage_bps="0",
    )
    ledger = AccountLedger(planner_ledger_path)
    ledger.init_schema()
    try:
        _ensure_operation_schema(broker.conn)
        prior = broker.conn.execute(
            "SELECT report_json FROM paper_operation_run WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if prior is not None:
            return json.loads(prior["report_json"])

        current_symbols = {row["symbol"] for row in broker.get_positions()}
        required_prices = current_symbols | set(target_weights)
        missing_prices = sorted(required_prices - set(prices))
        if missing_prices:
            raise ValueError(f"paper operation is missing prices: {missing_prices}")
        before = _portfolio_snapshot(broker, prices)
        nav = Decimal(before["nav_decimal"])
        if nav <= 0:
            raise ValueError("paper portfolio NAV must be positive")
        state = broker.conn.execute(
            "SELECT last_rebalance_effective_date FROM paper_strategy_state "
            "WHERE strategy = ?",
            (strategy,),
        ).fetchone()
        effective_date = str(rebalance.get("effective_date") or "")
        date.fromisoformat(effective_date)
        should_apply = state is None or str(
            state["last_rebalance_effective_date"] or ""
        ) < effective_date
        orders: list[dict[str, Any]] = []
        if should_apply:
            positions = {
                str(row["symbol"]): Decimal(str(row["quantity_decimal"]))
                for row in broker.get_positions()
            }
            deltas: list[tuple[str, Decimal, Decimal]] = []
            for symbol in sorted(set(positions) | set(target_weights)):
                current_value = positions.get(symbol, Decimal("0")) * prices[symbol]
                target_value = nav * target_weights.get(symbol, Decimal("0"))
                deltas.append((symbol, target_value - current_value, target_value))
            deltas.sort(key=lambda item: (item[1] > 0, item[0]))
            risk_hub = RiskHub(policy)
            planner = OrderPlanner()
            minimum_trade = Decimal(str(paper_policy["minimum_trade_notional_usd"]))
            for symbol, delta, target_value in deltas:
                if abs(delta) < minimum_trade:
                    continue
                side = "BUY" if delta > 0 else "SELL"
                sequence = len(orders) + 1
                client_order_id = (
                    "p-"
                    + hashlib.sha256(
                        f"{run_id}:{sequence}:{symbol}:{side}".encode("utf-8")
                    ).hexdigest()[:30]
                )
                if side == "SELL":
                    sizing = {
                        "client_order_id": client_order_id,
                        "qty": _decimal_text(abs(delta) / prices[symbol]),
                    }
                else:
                    cash = Decimal(broker.get_balances()["cash"].get("USD", "0"))
                    commission_rate = Decimal(str(cost_model.commission_bps)) / Decimal(
                        "10000"
                    )
                    affordable = max(
                        Decimal("0"),
                        (cash - Decimal(str(cost_model.minimum_commission_usd)))
                        / (Decimal("1") + commission_rate),
                    )
                    amount = min(delta, affordable)
                    if amount < minimum_trade:
                        continue
                    sizing = {
                        "client_order_id": client_order_id,
                        "order_amount": _decimal_text(amount),
                    }
                signal = Signal(
                    engine=f"paper:{strategy}",
                    symbol_or_pair=symbol,
                    side=side,
                    raw_score=float(target_weights.get(symbol, Decimal("0"))),
                    adjusted_score=None,
                    target_weight=float(target_weights.get(symbol, Decimal("0"))),
                    expected_max_loss=float(max(delta, Decimal("0"))),
                    reason_code="published_rebalance_infrastructure_validation",
                )
                intent = OrderIntent.create(
                    signal,
                    sizing,
                    account_seq="paper-usd-v1",
                    snapshot_run_id=source_run_id,
                    policy_hash=policy_hash,
                    currency="USD",
                    reference_price=prices[symbol],
                )
                current = _portfolio_snapshot(broker, prices)
                current_open_orders = broker.conn.execute(
                    "SELECT COUNT(*) FROM paper_order WHERE status NOT IN "
                    "('paper_filled','paper_cancelled')"
                ).fetchone()[0]
                decision = risk_hub.evaluate_signal(
                    signal,
                    {
                        "kill_switch_state": "NORMAL",
                        "reconciliation_ok": current_open_orders == 0,
                        "source_health_ok": True,
                        "rate_limit_ok": True,
                        "open_orders_count": current_open_orders,
                        "account_seq": "paper-usd-v1",
                        "snapshot_run_id": source_run_id,
                        "policy_hash": policy_hash,
                        "nav": float(Decimal(current["nav_decimal"])),
                        "drawdown_pct": 0.0,
                        "available_cash": float(
                            Decimal(current["cash_decimal"])
                        ),
                        "sellable_quantity": float(
                            next(
                                (
                                    Decimal(item["quantity_decimal"])
                                    for item in current["positions"]
                                    if item["symbol"] == symbol
                                ),
                                Decimal("0"),
                            )
                        ),
                        "allowed_symbols": required_prices,
                    },
                    order_intent=intent,
                    guardrail_profile="paper_guardrails",
                )
                plan = planner.create_plan(
                    signal,
                    sizing,
                    risk_decision=decision,
                    ledger=ledger,
                    account_seq="paper-usd-v1",
                    allowed_symbols=required_prices,
                    snapshot_run_id=source_run_id,
                    policy_hash=policy_hash,
                    currency="USD",
                    reference_price=prices[symbol],
                )
                broker.slippage_bps = Decimal(
                    str(cost_model.slippage_bps_for(float(Decimal(plan.notional_decimal))))
                )
                submitted = broker.submit_order(
                    {
                        "client_order_id": plan.client_order_id,
                        "symbol": plan.symbol,
                        "currency": plan.currency,
                        "side": plan.side,
                        "qty": plan.quantity_decimal,
                        "order_amount": plan.order_amount_decimal,
                    }
                )
                filled = broker.process_order(
                    plan.client_order_id,
                    market_price=_decimal_text(prices[symbol]),
                    settlement_date=_settlement_date(market_date),
                )
                orders.append(
                    {
                        "submitted": submitted,
                        "filled": filled,
                        "risk_reason": decision.reason,
                        "target_value_decimal": _decimal_text(target_value),
                    }
                )
            broker.conn.execute(
                """
                INSERT INTO paper_strategy_state(
                  strategy, last_rebalance_effective_date,
                  last_artifact_sha256, updated_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(strategy) DO UPDATE SET
                  last_rebalance_effective_date=excluded.last_rebalance_effective_date,
                  last_artifact_sha256=excluded.last_artifact_sha256,
                  updated_at=excluded.updated_at
                """,
                (strategy, effective_date, artifact_sha),
            )

        after = _portfolio_snapshot(broker, prices)
        integrity = broker.conn.execute("PRAGMA integrity_check").fetchone()[0]
        open_orders = broker.conn.execute(
            "SELECT COUNT(*) FROM paper_order WHERE status NOT IN "
            "('paper_filled','paper_cancelled')"
        ).fetchone()[0]
        reconciliation_ok = (
            integrity == "ok"
            and open_orders == 0
            and Decimal(after["cash_decimal"]) >= 0
            and all(
                Decimal(item["quantity_decimal"]) >= 0
                for item in after["positions"]
            )
        )
        report = {
            "schema_version": "persistent-paper-operation-v1",
            "run_id": run_id,
            "mode": "paper",
            "live_orders_enabled": False,
            "strategy": strategy,
            "source_run_id": source_run_id,
            "source_code_revision": source_revision,
            "market_date": market_date,
            "artifact_sha256": artifact_sha,
            "policy_hash": policy_hash,
            "rebalance": {
                "signal_date": rebalance.get("signal_date"),
                "effective_date": effective_date,
                "target_weights": {
                    symbol: _decimal_text(weight)
                    for symbol, weight in target_weights.items()
                },
                "applied_this_run": should_apply,
            },
            "cost_model": cost_model.as_record(),
            "evidence": {
                "eligible_for_strategy_promotion": evidence_eligible,
                "reason": (
                    "prospective_protocol_complete"
                    if evidence_eligible
                    else "infrastructure_validation_only"
                ),
            },
            "orders": orders,
            "before": before,
            "after": after,
            "reconciliation": {
                "status": "ok" if reconciliation_ok else "blocked",
                "sqlite_integrity": integrity,
                "open_order_count": open_orders,
            },
        }
        if not reconciliation_ok:
            raise RuntimeError("paper reconciliation is blocked")
        report_json = _canonical_json(report)
        report_sha = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
        broker.conn.execute(
            """
            INSERT INTO paper_operation_run(
              run_id, strategy, source_run_id, source_code_revision,
              market_date, artifact_sha256, policy_hash, status,
              evidence_eligible, report_json, report_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'complete', ?, ?, ?)
            """,
            (
                run_id,
                strategy,
                source_run_id,
                source_revision,
                market_date,
                artifact_sha,
                policy_hash,
                int(evidence_eligible),
                report_json,
                report_sha,
            ),
        )
        broker.conn.commit()
        return json.loads(report_json)
    finally:
        ledger.close()
        broker.close()
