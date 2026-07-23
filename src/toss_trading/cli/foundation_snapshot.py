from __future__ import annotations

import argparse
import os
from pathlib import Path

from toss_trading.account import AccountLedger, FoundationSnapshotter
from toss_trading.broker.credentials import load_toss_credentials_from_env
from toss_trading.broker.toss import TossReadOnlyAdapter
from toss_trading.data import (
    load_instrument_mappings,
    load_universe,
    validate_universe_mapping,
)
from toss_trading.runtime import JsonlLogger, load_gcp_secret_environment
from toss_trading.policy import load_policy


def _latest_source_health_safely(ledger: AccountLedger):
    try:
        return ledger.conn.execute(
            """
            SELECT source, channel, source_status, action
            FROM source_health_snapshot
            ORDER BY ts DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception:
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read Toss account state, store raw/normalized snapshots, and print an explanation.",
    )
    parser.add_argument(
        "--db",
        default="runtime/foundation_account_state.sqlite",
        help="SQLite database path for local foundation snapshots.",
    )
    parser.add_argument(
        "--schema",
        default="schemas/trading_ledger.sql",
        help="SQL schema path.",
    )
    parser.add_argument(
        "--universe",
        default="data/universe.csv",
        help="Universe CSV path.",
    )
    parser.add_argument(
        "--instrument-master",
        default="data/instrument_master.csv",
        help="Instrument master CSV path.",
    )
    parser.add_argument(
        "--include-sellable-quantity",
        action="store_true",
        default=True,
        help="Query sellable quantity for current holdings. Enabled by default.",
    )
    parser.add_argument(
        "--skip-sellable-quantity",
        action="store_false",
        dest="include_sellable_quantity",
        help="Skip sellable quantity queries.",
    )
    parser.add_argument(
        "--buying-power-currency",
        action="append",
        dest="buying_power_currencies",
        default=None,
        help=(
            "Currency query value for Toss buying-power. Repeat for multiple currencies. "
            "Holding currencies are added automatically; defaults to USD."
        ),
    )
    parser.add_argument(
        "--skip-order-details",
        action="store_false",
        dest="include_order_details",
        default=True,
        help="Skip /orders/{orderId} detail calls.",
    )
    parser.add_argument(
        "--include-closed-orders",
        action="store_true",
        help=(
            "Read CLOSED orders as an explicit diagnostic/recovery action. "
            "Disabled by default because the OpenAPI 1.2.4 schema text still conflicts "
            "with the verified server behavior."
        ),
    )
    parser.add_argument(
        "--max-order-pages",
        type=int,
        default=20,
        help="Maximum pages to read for each order status.",
    )
    parser.add_argument(
        "--max-order-details",
        type=int,
        default=20,
        help="Maximum /orders/{orderId} detail calls per snapshot.",
    )
    parser.add_argument(
        "--report",
        default="runtime/foundation_account_state_report.txt",
        help="Text report output path.",
    )
    parser.add_argument(
        "--load-gcp-secrets",
        action="store_true",
        help="Load Toss environment variables from GCP Secret Manager before running.",
    )
    parser.add_argument(
        "--gcp-project-id",
        default=None,
        help="GCP project id for Secret Manager. Defaults to GCP_PROJECT_ID.",
    )
    parser.add_argument(
        "--json-log",
        default=None,
        help="Optional JSONL log output path.",
    )
    parser.add_argument(
        "--policy",
        default="config/default_policy.yaml",
        help="Validated policy artifact recorded with this snapshot run.",
    )
    parser.add_argument(
        "--target-order-id",
        default=None,
        help="Known Toss orderId captured while the manual v1 test order was OPEN.",
    )
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("FOUNDATION_CODE_REVISION"),
        help="Immutable release or Git revision recorded in snapshot_run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    universe = load_universe(args.universe)
    mappings = load_instrument_mappings(args.instrument_master)
    validate_universe_mapping(universe, mappings)
    _, policy_hash = load_policy(args.policy)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    logger = JsonlLogger(args.json_log)
    logger.emit("foundation_snapshot_start", db=str(db_path), report=str(report_path))

    ledger = AccountLedger(db_path)
    try:
        ledger.init_schema(args.schema)
        if args.load_gcp_secrets:
            result = load_gcp_secret_environment(project_id=args.gcp_project_id)
            logger.emit(
                "gcp_secret_environment_loaded",
                loaded_env_names=result.loaded_env_names,
                skipped_env_names=result.skipped_env_names,
            )
        ledger.load_instrument_mappings(mappings)
        credentials = load_toss_credentials_from_env()
        adapter = TossReadOnlyAdapter(credentials, ledger)
        result = FoundationSnapshotter(adapter, ledger).snapshot(
            include_sellable_quantity=args.include_sellable_quantity,
            include_order_details=args.include_order_details,
            include_closed_orders=args.include_closed_orders,
            buying_power_currency=args.buying_power_currencies or ["USD"],
            max_order_pages=args.max_order_pages,
            max_order_details=args.max_order_details,
            target_order_id=args.target_order_id,
            policy_hash=policy_hash,
            code_revision=args.code_revision,
        )
        lines = [
            "foundation_snapshot=ok",
            f"accounts_rows={result.accounts}",
            f"snapshot_run_id={result.run_id}",
            f"holdings_rows={result.holdings}",
            f"open_order_rows={result.open_orders}",
            f"closed_order_rows={result.closed_orders}",
            f"buying_power_rows={result.buying_power_rows}",
            f"commission_rows={result.commission_rows}",
            f"sellable_quantity_rows={result.sellable_quantity_rows}",
            f"order_detail_rows={result.order_detail_rows}",
            f"execution_snapshot_rows={result.execution_snapshot_rows}",
            f"execution_delta_rows={result.execution_delta_rows}",
            f"cash_event_rows={result.cash_event_rows}",
            "",
            result.explanation.as_text(),
        ]
        report = "\n".join(lines)
        report_path.write_text(report + "\n", encoding="utf-8")
        print(report)
        logger.emit(
            "foundation_snapshot_ok",
            accounts_rows=result.accounts,
            holdings_rows=result.holdings,
            open_order_rows=result.open_orders,
            closed_order_rows=result.closed_orders,
            buying_power_rows=result.buying_power_rows,
            commission_rows=result.commission_rows,
            sellable_quantity_rows=result.sellable_quantity_rows,
            order_detail_rows=result.order_detail_rows,
            execution_snapshot_rows=result.execution_snapshot_rows,
            execution_delta_rows=result.execution_delta_rows,
            cash_event_rows=result.cash_event_rows,
        )
        return 0
    except Exception as exc:
        # Keep this intentionally terse; raw API responses are already persisted.
        lines = ["foundation_snapshot=failed", f"reason={exc}"]
        health = _latest_source_health_safely(ledger)
        if health is not None:
            lines.extend(
                [
                    f"source_health={health['source']} {health['channel']} {health['source_status']}",
                    f"action={health['action'] or 'inspect_failure'}",
                ]
            )
        message = "\n".join(lines)
        report_path.write_text(message + "\n", encoding="utf-8")
        print(message)
        logger.emit("foundation_snapshot_failed", reason=str(exc))
        return 1
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
