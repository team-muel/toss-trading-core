from __future__ import annotations

import argparse
from pathlib import Path

from toss_trading.account import AccountLedger, FoundationSnapshotter
from toss_trading.broker.credentials import load_toss_credentials_from_env
from toss_trading.broker.toss import TossReadOnlyAdapter
from toss_trading.data import (
    load_instrument_mappings,
    load_universe,
    validate_universe_mapping,
)


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
        default="USD",
        help="Currency query value for Toss buying-power. Defaults to USD.",
    )
    parser.add_argument(
        "--report",
        default="runtime/foundation_account_state_report.txt",
        help="Text report output path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    universe = load_universe(args.universe)
    mappings = load_instrument_mappings(args.instrument_master)
    validate_universe_mapping(universe, mappings)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    ledger = AccountLedger(db_path)
    try:
        ledger.init_schema(args.schema)
        ledger.load_instrument_mappings(mappings)
        credentials = load_toss_credentials_from_env()
        adapter = TossReadOnlyAdapter(credentials, ledger)
        result = FoundationSnapshotter(adapter, ledger).snapshot(
            include_sellable_quantity=args.include_sellable_quantity,
            buying_power_currency=args.buying_power_currency,
        )
        lines = [
            "foundation_snapshot=ok",
            f"accounts_rows={result.accounts}",
            f"holdings_rows={result.holdings}",
            f"open_order_rows={result.open_orders}",
            f"closed_order_rows={result.closed_orders}",
            f"buying_power_rows={result.buying_power_rows}",
            f"commission_rows={result.commission_rows}",
            f"sellable_quantity_rows={result.sellable_quantity_rows}",
            "",
            result.explanation.as_text(),
        ]
        report = "\n".join(lines)
        report_path.write_text(report + "\n", encoding="utf-8")
        print(report)
        return 0
    except Exception as exc:
        # Keep this intentionally terse; raw API responses are already persisted.
        lines = ["foundation_snapshot=failed", f"reason={exc}"]
        health = ledger.conn.execute(
            """
            SELECT source, channel, source_status, action
            FROM source_health_snapshot
            ORDER BY ts DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
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
        return 1
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
