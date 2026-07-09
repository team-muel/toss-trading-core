from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from toss_trading.account import AccountLedger
from toss_trading.data import (
    load_instrument_mappings,
    load_universe,
    validate_universe_mapping,
)
from toss_trading.runtime import JsonlLogger


REQUIRED_RAW_ENDPOINTS = [
    "/oauth2/token",
    "/api/v1/accounts",
    "/api/v1/holdings",
    "/api/v1/orders?status=OPEN",
    "/api/v1/orders?status=CLOSED",
    "/api/v1/buying-power",
    "/api/v1/commissions",
]

KNOWN_ORDER_STATUSES = {
    "PENDING",
    "PENDING_CANCEL",
    "PENDING_REPLACE",
    "PARTIAL_FILLED",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "CANCEL_REJECTED",
    "REPLACE_REJECTED",
    "REPLACED",
}

REVIEW_ORDER_STATUSES = {"CANCEL_REJECTED", "REPLACE_REJECTED"}


@dataclass(frozen=True)
class FoundationAuditResult:
    ok: bool
    lines: list[str]

    def as_text(self) -> str:
        return "\n".join(self.lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit whether the foundation account-state snapshot is complete.",
    )
    parser.add_argument(
        "--db",
        default="runtime/foundation_account_state.sqlite",
        help="SQLite database path to audit.",
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
        "--profile",
        choices=["v0-empty-safe", "v1-funded-read-only"],
        default="v0-empty-safe",
        help="Validation profile. v1 requires funded/non-empty account evidence.",
    )
    parser.add_argument(
        "--json-log",
        default=None,
        help="Optional JSONL log output path.",
    )
    return parser


def _count(conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(query, params).fetchone()[0])


def _raw_success_count(conn: sqlite3.Connection, endpoint: str) -> int:
    separator = "&" if "?" in endpoint else "?"
    return _count(
        conn,
        """
        SELECT COUNT(*)
        FROM raw_api_response
        WHERE (endpoint = ? OR endpoint LIKE ?)
          AND (status_code IS NULL OR status_code < 400)
        """,
        (endpoint, f"{endpoint}{separator}%"),
    )


def audit_foundation_db(
    *,
    db_path: str | Path,
    universe_path: str | Path = "data/universe.csv",
    instrument_master_path: str | Path = "data/instrument_master.csv",
    profile: str = "v0-empty-safe",
) -> FoundationAuditResult:
    failures: list[str] = []
    lines = ["foundation_audit=running", f"profile={profile}"]

    universe = load_universe(universe_path)
    mappings = load_instrument_mappings(instrument_master_path)
    validate_universe_mapping(universe, mappings)

    ledger = AccountLedger(db_path)
    try:
        conn = ledger.conn
        instrument_count = _count(conn, "SELECT COUNT(DISTINCT ticker) FROM instrument_master")
        lines.append(f"instrument_master_tickers={instrument_count}")
        if instrument_count < len(universe):
            failures.append("instrument_master_not_loaded_for_full_universe")

        for endpoint in REQUIRED_RAW_ENDPOINTS:
            successes = _raw_success_count(conn, endpoint)
            lines.append(f"raw_success[{endpoint}]={successes}")
            if successes == 0:
                failures.append(f"missing_successful_raw_endpoint:{endpoint}")

        latest_bad_health = conn.execute(
            """
            SELECT source, channel, source_status, action
            FROM source_health_snapshot current
            WHERE source_status <> 'ok'
              AND NOT EXISTS (
                SELECT 1
                FROM source_health_snapshot newer
                WHERE newer.source = current.source
                  AND newer.channel = current.channel
                  AND newer.ts > current.ts
              )
            ORDER BY ts DESC
            LIMIT 1
            """
        ).fetchone()
        if latest_bad_health is not None:
            failures.append(
                "latest_source_health_not_ok:"
                f"{latest_bad_health['source']}:{latest_bad_health['channel']}:"
                f"{latest_bad_health['source_status']}:{latest_bad_health['action']}"
            )

        account_rows = conn.execute(
            "SELECT DISTINCT account_seq FROM account_snapshot ORDER BY account_seq"
        ).fetchall()
        lines.append(f"account_count={len(account_rows)}")
        if not account_rows:
            failures.append("missing_account_snapshot")

        for row in account_rows:
            account_seq = str(row["account_seq"])
            explanation = ledger.explain_account_state(account_seq)
            closed_order_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM broker_order_snapshot
                WHERE account_seq = ?
                  AND status NOT IN ('PENDING', 'PENDING_CANCEL', 'PENDING_REPLACE', 'PARTIAL_FILLED')
                """,
                (account_seq,),
            )
            order_detail_raw_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM raw_api_response
                WHERE account_seq = ?
                  AND endpoint LIKE '/api/v1/orders/%'
                  AND (status_code IS NULL OR status_code < 400)
                """,
                (account_seq,),
            )
            execution_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM execution_snapshot_log
                WHERE account_seq = ?
                  AND cumulative_filled_qty IS NOT NULL
                  AND cumulative_filled_amount IS NOT NULL
                  AND average_filled_price IS NOT NULL
                """,
                (account_seq,),
            )
            execution_delta_rows = _count(
                conn,
                "SELECT COUNT(*) FROM execution_delta_log WHERE account_seq = ?",
                (account_seq,),
            )
            settlement_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM broker_order_snapshot
                WHERE account_seq = ? AND settlement_date IS NOT NULL
                """,
                (account_seq,),
            )
            commission_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM commission_snapshot
                WHERE account_seq = ?
                  AND commission_amount IS NOT NULL
                """,
                (account_seq,),
            )
            sellable_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM sellable_quantity_snapshot
                WHERE account_seq = ?
                """,
                (account_seq,),
            )
            reconciliation_block_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM broker_reconciliation_log
                WHERE account_seq = ?
                  AND status = 'BLOCK'
                """,
                (account_seq,),
            )
            latest_reconciliation_block = conn.execute(
                """
                SELECT item_type, difference, action_required
                FROM broker_reconciliation_log
                WHERE account_seq = ?
                  AND status = 'BLOCK'
                ORDER BY ts DESC, created_at DESC
                LIMIT 1
                """,
                (account_seq,),
            ).fetchone()
            unknown_order_status_rows = conn.execute(
                """
                SELECT DISTINCT status
                FROM broker_order_snapshot
                WHERE account_seq = ?
                  AND status NOT IN ({placeholders})
                ORDER BY status
                """.format(placeholders=", ".join("?" for _ in KNOWN_ORDER_STATUSES)),
                (account_seq, *sorted(KNOWN_ORDER_STATUSES)),
            ).fetchall()
            review_order_status_rows = conn.execute(
                """
                SELECT DISTINCT status
                FROM broker_order_snapshot
                WHERE account_seq = ?
                  AND status IN ({placeholders})
                ORDER BY status
                """.format(placeholders=", ".join("?" for _ in REVIEW_ORDER_STATUSES)),
                (account_seq, *sorted(REVIEW_ORDER_STATUSES)),
            ).fetchall()
            unknown_order_statuses = [str(item["status"]) for item in unknown_order_status_rows]
            review_order_statuses = [str(item["status"]) for item in review_order_status_rows]
            lines.append(f"account[{account_seq}].holdings_count={explanation.holdings_count}")
            lines.append(
                f"account[{account_seq}].open_orders_count={explanation.open_orders_count}"
            )
            lines.append(f"account[{account_seq}].closed_order_rows={closed_order_rows}")
            lines.append(f"account[{account_seq}].order_detail_raw_rows={order_detail_raw_rows}")
            lines.append(f"account[{account_seq}].execution_rows={execution_rows}")
            lines.append(f"account[{account_seq}].execution_delta_rows={execution_delta_rows}")
            lines.append(f"account[{account_seq}].commission_rows={commission_rows}")
            lines.append(f"account[{account_seq}].settlement_rows={settlement_rows}")
            lines.append(f"account[{account_seq}].sellable_quantity_rows={sellable_rows}")
            lines.append(
                f"account[{account_seq}].reconciliation_block_rows="
                f"{reconciliation_block_rows}"
            )
            lines.append(
                f"account[{account_seq}].unknown_order_statuses="
                f"{unknown_order_statuses or ['none']}"
            )
            lines.append(
                f"account[{account_seq}].review_order_statuses="
                f"{review_order_statuses or ['none']}"
            )
            lines.append(
                f"account[{account_seq}].buying_power_currencies="
                f"{sorted(explanation.buying_power_by_currency)}"
            )
            lines.append(f"account[{account_seq}].blockers={explanation.blockers or ['none']}")
            if explanation.holdings_count > 0 and sellable_rows == 0:
                failures.append(f"account[{account_seq}].missing_sellable_quantity_snapshot")
            if not explanation.buying_power_by_currency:
                failures.append(f"account[{account_seq}].missing_normalized_buying_power")
            if explanation.blockers:
                failures.extend(f"account[{account_seq}].{item}" for item in explanation.blockers)
            if latest_reconciliation_block is not None:
                failures.append(
                    f"account[{account_seq}].broker_reconciliation_block:"
                    f"{latest_reconciliation_block['item_type']}:"
                    f"{latest_reconciliation_block['difference']}:"
                    f"{latest_reconciliation_block['action_required']}"
                )
            for status in unknown_order_statuses:
                failures.append(f"account[{account_seq}].unknown_order_status:{status}")
            for status in review_order_statuses:
                failures.append(
                    f"account[{account_seq}].review_order_status_requires_order_detail:{status}"
                )
            if profile == "v1-funded-read-only":
                if explanation.holdings_count == 0:
                    failures.append(f"account[{account_seq}].v1_requires_nonzero_holdings")
                if closed_order_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_closed_order")
                if order_detail_raw_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_order_detail_raw")
                if execution_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_execution_summary")
                if execution_delta_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_execution_delta")
                if commission_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_commission_snapshot")
                if settlement_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_settlement_date")
                if sellable_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_sellable_quantity")

        if failures:
            return FoundationAuditResult(
                ok=False,
                lines=["foundation_audit=failed", *lines[1:], *[f"failure={x}" for x in failures]],
            )
        return FoundationAuditResult(ok=True, lines=["foundation_audit=ok", *lines[1:]])
    finally:
        ledger.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = JsonlLogger(args.json_log)
    logger.emit("foundation_audit_start", db=args.db, profile=args.profile)
    result = audit_foundation_db(
        db_path=args.db,
        universe_path=args.universe,
        instrument_master_path=args.instrument_master,
        profile=args.profile,
    )
    print(result.as_text())
    logger.emit(
        "foundation_audit_ok" if result.ok else "foundation_audit_failed",
        profile=args.profile,
        lines=result.lines,
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
