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
        choices=[
            "v0-empty-safe",
            "v1-funded-read-only",
            "v2-live-readiness",
        ],
        default="v0-empty-safe",
        help=(
            "Validation profile. v1 requires funded account evidence; v2 also "
            "requires immutable code/policy provenance, CLOSED continuity, and "
            "cash-ledger genesis for every buying-power currency."
        ),
    )
    parser.add_argument(
        "--json-log",
        default=None,
        help="Optional JSONL log output path.",
    )
    return parser


def _count(conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(query, params).fetchone()[0])


def _raw_success_count(
    conn: sqlite3.Connection,
    endpoint: str,
    *,
    run_id: str,
    account_seq: str,
) -> int:
    separator = "&" if "?" in endpoint else "?"
    account_neutral = endpoint in {"/oauth2/token", "/api/v1/accounts"}
    return _count(
        conn,
        """
        SELECT COUNT(*)
        FROM raw_api_response
        WHERE (endpoint = ? OR endpoint LIKE ?)
          AND run_id = ?
          AND status_code BETWEEN 200 AND 299
          AND (? = 1 OR account_seq = ?)
        """,
        (endpoint, f"{endpoint}{separator}%", run_id, int(account_neutral), account_seq),
    )


def _schema_failure(db_path: str | Path) -> str | None:
    path = Path(db_path)
    if not path.exists():
        return "foundation_database_not_found"
    conn = sqlite3.connect(path)
    try:
        required_tables = {
            "snapshot_run",
            "raw_api_response",
            "source_health_snapshot",
            "instrument_master",
            "account_snapshot",
            "holding_snapshot",
            "broker_order_snapshot",
            "buying_power_snapshot",
            "sellable_quantity_snapshot",
            "execution_snapshot_log",
            "execution_delta_log",
            "cash_ledger",
            "cash_ledger_genesis",
            "broker_reconciliation_log",
        }
        existing = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing = sorted(required_tables - existing)
        if missing:
            return f"incompatible_foundation_schema:missing_tables={','.join(missing)}"
        run_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(snapshot_run)")
        }
        required_run_columns = {
            "run_id",
            "account_seq",
            "target_order_id",
            "started_at",
            "completed_at",
            "status",
            "policy_hash",
            "code_revision",
        }
        missing_columns = sorted(required_run_columns - run_columns)
        if missing_columns:
            return f"incompatible_foundation_schema:missing_snapshot_run_columns={','.join(missing_columns)}"
    finally:
        conn.close()
    return None


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

    schema_failure = _schema_failure(db_path)
    if schema_failure is not None:
        return FoundationAuditResult(
            ok=False,
            lines=["foundation_audit=failed", f"profile={profile}", f"failure={schema_failure}"],
        )

    ledger = AccountLedger(db_path)
    try:
        conn = ledger.conn
        instrument_count = _count(conn, "SELECT COUNT(DISTINCT ticker) FROM instrument_master")
        lines.append(f"instrument_master_tickers={instrument_count}")
        if instrument_count < len(universe):
            failures.append("instrument_master_not_loaded_for_full_universe")

        run = conn.execute(
            """
            SELECT *
            FROM snapshot_run
            WHERE status = 'COMPLETE'
            ORDER BY completed_at DESC, started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if run is None:
            return FoundationAuditResult(
                ok=False,
                lines=[
                    "foundation_audit=failed",
                    f"profile={profile}",
                    "failure=missing_complete_snapshot_run",
                ],
            )
        run_id = str(run["run_id"])
        account_seq = str(run["account_seq"] or "").strip()
        target_order_id = str(run["target_order_id"] or "").strip()
        policy_hash = str(run["policy_hash"] or "").strip()
        code_revision = str(run["code_revision"] or "").strip()
        lines.extend(
            [
                f"snapshot_run_id={run_id}",
                f"snapshot_account_seq={account_seq or 'none'}",
                f"target_order_id={'present' if target_order_id else 'none'}",
                f"policy_hash={'present' if policy_hash else 'none'}",
                f"code_revision={code_revision or 'none'}",
            ]
        )
        if not account_seq:
            failures.append("complete_snapshot_run_missing_account_seq")

        for endpoint in REQUIRED_RAW_ENDPOINTS:
            successes = _raw_success_count(
                conn,
                endpoint,
                run_id=run_id,
                account_seq=account_seq,
            )
            lines.append(f"raw_success[{endpoint}]={successes}")
            if successes == 0:
                failures.append(f"missing_successful_raw_endpoint:{endpoint}")

        failed_broker_rows = _count(
            conn,
            """
            SELECT COUNT(*) FROM raw_api_response
            WHERE run_id = ? AND source_type = 'broker'
              AND (status_code IS NULL OR status_code NOT BETWEEN 200 AND 299)
            """,
            (run_id,),
        )
        lines.append(f"non_2xx_broker_rows={failed_broker_rows}")
        if failed_broker_rows:
            failures.append("latest_complete_run_contains_non_2xx_broker_response")

        latest_bad_health = conn.execute(
            """
            SELECT source, channel, source_status, action
            FROM source_health_snapshot
            WHERE run_id = ? AND source_status <> 'ok'
            ORDER BY ts DESC, created_at DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if latest_bad_health is not None:
            failures.append(
                "latest_source_health_not_ok:"
                f"{latest_bad_health['source']}:{latest_bad_health['channel']}:"
                f"{latest_bad_health['source_status']}:{latest_bad_health['action']}"
            )

        account_rows = conn.execute(
            "SELECT DISTINCT account_seq FROM account_snapshot WHERE run_id = ? ORDER BY account_seq",
            (run_id,),
        ).fetchall()
        lines.append(f"account_count={len(account_rows)}")
        if not account_rows:
            failures.append("missing_account_snapshot")
        account_values = [str(row["account_seq"]) for row in account_rows]
        if account_seq and account_seq not in account_values:
            failures.append("snapshot_run_account_not_in_current_account_snapshot")
        if len(account_values) != 1:
            failures.append("latest_complete_run_must_bind_exactly_one_account")

        if account_seq:
            explanation = ledger.explain_account_state(account_seq, run_id=run_id)
            closed_order_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM broker_order_snapshot
                WHERE run_id = ? AND account_seq = ?
                  AND status NOT IN ('PENDING', 'PENDING_CANCEL', 'PENDING_REPLACE', 'PARTIAL_FILLED')
                """,
                (run_id, account_seq),
            )
            order_detail_raw_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM raw_api_response
                WHERE run_id = ? AND account_seq = ?
                  AND endpoint LIKE '/api/v1/orders/%'
                  AND status_code BETWEEN 200 AND 299
                """,
                (run_id, account_seq),
            )
            target_detail_raw_rows = 0
            target_order_rows = 0
            target_filled_rows = 0
            if target_order_id:
                target_detail_raw_rows = _count(
                    conn,
                    """
                    SELECT COUNT(*) FROM raw_api_response
                    WHERE run_id = ? AND account_seq = ? AND endpoint = ?
                      AND status_code BETWEEN 200 AND 299
                    """,
                    (run_id, account_seq, f"/api/v1/orders/{target_order_id}"),
                )
                target_order_rows = _count(
                    conn,
                    """
                    SELECT COUNT(*) FROM broker_order_snapshot
                    WHERE run_id = ? AND account_seq = ? AND broker_order_id = ?
                    """,
                    (run_id, account_seq, target_order_id),
                )
                target_filled_rows = _count(
                    conn,
                    """
                    SELECT COUNT(*) FROM broker_order_snapshot
                    WHERE run_id = ? AND account_seq = ? AND broker_order_id = ?
                      AND CAST(cumulative_filled_qty_decimal AS NUMERIC) > 0
                    """,
                    (run_id, account_seq, target_order_id),
                )
            execution_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM execution_snapshot_log
                WHERE run_id = ? AND account_seq = ?
                  AND (? = '' OR order_id = ?)
                  AND cumulative_filled_qty_decimal IS NOT NULL
                  AND cumulative_filled_amount_decimal IS NOT NULL
                  AND average_filled_price_decimal IS NOT NULL
                """,
                (run_id, account_seq, target_order_id, target_order_id),
            )
            execution_delta_rows = _count(
                conn,
                """
                SELECT COUNT(*) FROM execution_delta_log
                WHERE run_id = ? AND account_seq = ? AND (? = '' OR order_id = ?)
                """,
                (run_id, account_seq, target_order_id, target_order_id),
            )
            cash_event_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM cash_ledger AS c
                JOIN execution_delta_log AS d ON d.id = c.source_ref
                WHERE d.run_id = ? AND d.account_seq = ?
                  AND (? = '' OR d.order_id = ?)
                """,
                (run_id, account_seq, target_order_id, target_order_id),
            )
            cash_event_gaps = ledger.cash_event_gaps(account_seq=account_seq)
            genesis_currencies = {
                str(row["currency"])
                for row in conn.execute(
                    """
                    SELECT currency
                    FROM cash_ledger_genesis
                    WHERE account_seq = ?
                    """,
                    (account_seq,),
                )
            }
            closed_list_raw_rows = _raw_success_count(
                conn,
                "/api/v1/orders?status=CLOSED",
                run_id=run_id,
                account_seq=account_seq,
            )
            reserved_cash = ledger.reserved_open_buy_cash(
                account_seq=account_seq,
                run_id=run_id,
            )
            settlement_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM broker_order_snapshot
                WHERE run_id = ? AND account_seq = ?
                  AND (? = '' OR broker_order_id = ?)
                  AND settlement_date IS NOT NULL
                """,
                (run_id, account_seq, target_order_id, target_order_id),
            )
            commission_rows = _count(
                conn,
                """
                SELECT COUNT(*) FROM broker_order_snapshot
                WHERE run_id = ? AND account_seq = ?
                  AND (? = '' OR broker_order_id = ?)
                  AND cumulative_commission_decimal IS NOT NULL
                """,
                (run_id, account_seq, target_order_id, target_order_id),
            )
            sellable_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM sellable_quantity_snapshot
                WHERE run_id = ? AND account_seq = ?
                """,
                (run_id, account_seq),
            )
            reconciliation_block_rows = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM broker_reconciliation_log
                WHERE account_seq = ?
                  AND status = 'BLOCK'
                  AND resolved_at IS NULL
                """,
                (account_seq,),
            )
            latest_reconciliation_block = conn.execute(
                """
                SELECT id, item_type, difference, action_required
                FROM broker_reconciliation_log
                WHERE account_seq = ?
                  AND status = 'BLOCK'
                  AND resolved_at IS NULL
                ORDER BY ts DESC, created_at DESC
                LIMIT 1
                """,
                (account_seq,),
            ).fetchone()
            unknown_order_status_rows = conn.execute(
                """
                SELECT DISTINCT status
                FROM broker_order_snapshot
                WHERE run_id = ? AND account_seq = ?
                  AND status NOT IN ({placeholders})
                ORDER BY status
                """.format(placeholders=", ".join("?" for _ in KNOWN_ORDER_STATUSES)),
                (run_id, account_seq, *sorted(KNOWN_ORDER_STATUSES)),
            ).fetchall()
            review_order_status_rows = conn.execute(
                """
                SELECT DISTINCT status
                FROM broker_order_snapshot
                WHERE run_id = ? AND account_seq = ?
                  AND status IN ({placeholders})
                ORDER BY status
                """.format(placeholders=", ".join("?" for _ in REVIEW_ORDER_STATUSES)),
                (run_id, account_seq, *sorted(REVIEW_ORDER_STATUSES)),
            ).fetchall()
            unknown_order_statuses = [str(item["status"]) for item in unknown_order_status_rows]
            review_order_statuses = [str(item["status"]) for item in review_order_status_rows]
            lines.append(f"account[{account_seq}].holdings_count={explanation.holdings_count}")
            lines.append(
                f"account[{account_seq}].open_orders_count={explanation.open_orders_count}"
            )
            lines.append(f"account[{account_seq}].closed_order_rows={closed_order_rows}")
            lines.append(f"account[{account_seq}].order_detail_raw_rows={order_detail_raw_rows}")
            lines.append(f"account[{account_seq}].target_detail_raw_rows={target_detail_raw_rows}")
            lines.append(f"account[{account_seq}].target_order_rows={target_order_rows}")
            lines.append(f"account[{account_seq}].target_filled_rows={target_filled_rows}")
            lines.append(f"account[{account_seq}].execution_rows={execution_rows}")
            lines.append(f"account[{account_seq}].execution_delta_rows={execution_delta_rows}")
            lines.append(f"account[{account_seq}].cash_event_rows={cash_event_rows}")
            lines.append(
                f"account[{account_seq}].cash_event_gaps={cash_event_gaps or ['none']}"
            )
            lines.append(
                f"account[{account_seq}].cash_genesis_currencies="
                f"{sorted(genesis_currencies)}"
            )
            lines.append(
                f"account[{account_seq}].closed_list_raw_rows={closed_list_raw_rows}"
            )
            lines.append(
                f"account[{account_seq}].reserved_open_buy_cash="
                f"{reserved_cash.amount_by_currency}"
            )
            lines.append(
                f"account[{account_seq}].reserved_cash_blockers="
                f"{reserved_cash.blockers or ['none']}"
            )
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
            failures.extend(
                f"account[{account_seq}].{item}" for item in reserved_cash.blockers
            )
            failures.extend(
                f"account[{account_seq}].{item}" for item in cash_event_gaps
            )
            if latest_reconciliation_block is not None:
                failures.append(
                    f"account[{account_seq}].broker_reconciliation_block:"
                    f"{latest_reconciliation_block['id']}:"
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
            if profile in {"v1-funded-read-only", "v2-live-readiness"}:
                if not target_order_id:
                    failures.append(f"account[{account_seq}].v1_requires_target_order_id")
                if explanation.holdings_count == 0:
                    failures.append(f"account[{account_seq}].v1_requires_nonzero_holdings")
                if target_detail_raw_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_target_order_detail_raw")
                if target_order_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_target_order_snapshot")
                if target_filled_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_filled_target_order")
                if execution_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_execution_summary")
                if execution_delta_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_execution_delta")
                if commission_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_execution_commission")
                if settlement_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_settlement_date")
                if sellable_rows == 0:
                    failures.append(f"account[{account_seq}].v1_requires_sellable_quantity")
            if profile == "v2-live-readiness":
                buying_power_currencies = set(explanation.buying_power_by_currency)
                missing_genesis = sorted(
                    buying_power_currencies - genesis_currencies
                )
                if missing_genesis:
                    failures.append(
                        f"account[{account_seq}].v2_missing_cash_genesis:"
                        f"{','.join(missing_genesis)}"
                    )
                if closed_list_raw_rows == 0:
                    failures.append(
                        f"account[{account_seq}].v2_requires_closed_order_continuity"
                    )
                if not policy_hash:
                    failures.append(
                        f"account[{account_seq}].v2_requires_policy_hash"
                    )
                if not code_revision or code_revision.lower() == "unknown":
                    failures.append(
                        f"account[{account_seq}].v2_requires_immutable_code_revision"
                    )

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
