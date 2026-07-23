from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from toss_trading.account.ledger import AccountLedger
from toss_trading.data import load_instrument_mappings


def _response_hash(body: Any) -> str:
    payload = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _accounts_for_bound_run(body: Any, account_seq: str) -> Any:
    """Restore the run's internal account key after raw-response redaction.

    Raw Toss account responses intentionally redact account identifiers before
    persistence. The completed snapshot_run retains only the approved internal
    account_seq reconciliation key, so replay may restore that one key without
    recovering or exposing an account number.
    """

    restored = copy.deepcopy(body)
    if not isinstance(restored, dict):
        return restored
    result = restored.get("result")
    if not isinstance(result, list):
        return restored
    account_items = [item for item in result if isinstance(item, dict)]
    if len(account_items) != 1:
        raise ValueError(
            "redacted accounts response is ambiguous; replay requires exactly one account"
        )
    account_items[0]["accountSeq"] = account_seq
    return restored


@dataclass(frozen=True)
class FoundationReplayResult:
    source_run_id: str
    replay_run_id: str
    account_seq: str
    raw_rows: int
    account_rows: int
    holding_rows: int
    order_rows: int
    buying_power_rows: int
    commission_rows: int
    sellable_quantity_rows: int
    execution_snapshot_rows: int
    execution_delta_rows: int
    cash_event_rows: int

    def as_text(self) -> str:
        return "\n".join(
            [
                "foundation_replay=ok",
                f"source_run_id={self.source_run_id}",
                f"replay_run_id={self.replay_run_id}",
                f"account_seq={self.account_seq}",
                f"raw_rows={self.raw_rows}",
                f"account_rows={self.account_rows}",
                f"holding_rows={self.holding_rows}",
                f"order_rows={self.order_rows}",
                f"buying_power_rows={self.buying_power_rows}",
                f"commission_rows={self.commission_rows}",
                f"sellable_quantity_rows={self.sellable_quantity_rows}",
                f"execution_snapshot_rows={self.execution_snapshot_rows}",
                f"execution_delta_rows={self.execution_delta_rows}",
                f"cash_event_rows={self.cash_event_rows}",
            ]
        )


def replay_foundation_run(
    *,
    source_db_path: str | Path,
    destination_db_path: str | Path,
    source_run_id: str | None = None,
    instrument_master_path: str | Path = "data/instrument_master.csv",
) -> FoundationReplayResult:
    """Rebuild normalized Foundation rows from stored raw broker responses.

    The destination must be a new database. This prevents a replay from
    accidentally mutating the source or an active runtime database.
    """

    source_path = Path(source_db_path)
    destination_path = Path(destination_db_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"source Foundation database not found: {source_path}")
    if destination_path.exists():
        raise FileExistsError(
            f"replay destination already exists; choose a new path: {destination_path}"
        )
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    destination: AccountLedger | None = None
    try:
        if source_run_id:
            run = source.execute(
                "SELECT * FROM snapshot_run WHERE run_id = ? AND status = 'COMPLETE'",
                (source_run_id,),
            ).fetchone()
        else:
            run = source.execute(
                """
                SELECT *
                FROM snapshot_run
                WHERE status = 'COMPLETE'
                ORDER BY completed_at DESC, started_at DESC
                LIMIT 1
                """
            ).fetchone()
        if run is None:
            raise ValueError("no matching complete Foundation snapshot run")

        resolved_source_run_id = str(run["run_id"])
        account_seq = str(run["account_seq"] or "").strip()
        if not account_seq:
            raise ValueError("source snapshot run is not bound to an account")

        raw_rows = source.execute(
            """
            SELECT *
            FROM raw_api_response
            WHERE run_id = ? AND source_type = 'broker'
            ORDER BY ts, created_at, id
            """,
            (resolved_source_run_id,),
        ).fetchall()
        if not raw_rows:
            raise ValueError("source snapshot run has no raw broker responses")

        destination = AccountLedger(destination_path)
        destination.init_schema()
        destination.load_instrument_mappings(
            load_instrument_mappings(instrument_master_path)
        )
        replay_run_id = destination.begin_snapshot_run(
            account_seq=account_seq,
            target_order_id=run["target_order_id"],
            policy_hash=run["policy_hash"],
            code_revision=f"raw-replay:{resolved_source_run_id}",
        )

        counts = {
            "accounts": 0,
            "holdings": 0,
            "orders": 0,
            "buying_power": 0,
            "commissions": 0,
            "sellable": 0,
            "execution_snapshots": 0,
            "execution_deltas": 0,
        }
        health_channels: set[str] = set()

        for row in raw_rows:
            status_code = row["status_code"]
            if status_code is None or not 200 <= int(status_code) <= 299:
                raise ValueError(
                    f"replay requires only successful broker responses: {row['endpoint']}"
                )
            try:
                body = json.loads(str(row["body_json"]))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid stored JSON for {row['endpoint']}") from exc
            if _response_hash(body) != row["response_hash"]:
                raise ValueError(f"stored response hash mismatch: {row['endpoint']}")

            endpoint = str(row["endpoint"])
            parsed = urllib.parse.urlsplit(endpoint)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            replay_raw_id = destination.save_raw_api_response(
                source=str(row["source"]),
                source_type=str(row["source_type"]),
                endpoint=endpoint,
                http_method=str(row["http_method"]),
                body=body,
                account_seq=row["account_seq"],
                status_code=int(status_code),
                request_id=row["request_id"],
                channel=row["channel"],
                ts=str(row["ts"]),
                run_id=replay_run_id,
            )

            if path == "/oauth2/token":
                continue
            if path == "/api/v1/accounts":
                replay_accounts_body = _accounts_for_bound_run(body, account_seq)
                counts["accounts"] += destination.ingest_accounts(
                    replay_accounts_body,
                    raw_ref=replay_raw_id,
                    ts=str(row["ts"]),
                    run_id=replay_run_id,
                )
            elif path == "/api/v1/holdings":
                counts["holdings"] += destination.ingest_holdings(
                    body,
                    account_seq=account_seq,
                    raw_ref=replay_raw_id,
                    ts=str(row["ts"]),
                    run_id=replay_run_id,
                )
            elif path == "/api/v1/orders":
                status_group = str((query.get("status") or [""])[0]).upper()
                if status_group not in {"OPEN", "CLOSED"}:
                    raise ValueError(f"stored order list has no valid status group: {endpoint}")
                counts["orders"] += destination.ingest_orders(
                    body,
                    account_seq=account_seq,
                    raw_ref=replay_raw_id,
                    ts=str(row["ts"]),
                    run_id=replay_run_id,
                    status_group=status_group,
                )
                snapshots, deltas = destination.ingest_execution_snapshots(
                    body,
                    account_seq=account_seq,
                    raw_ref=replay_raw_id,
                    ts=str(row["ts"]),
                    run_id=replay_run_id,
                    status_group=status_group,
                )
                counts["execution_snapshots"] += snapshots
                counts["execution_deltas"] += deltas
            elif path.startswith("/api/v1/orders/"):
                counts["orders"] += destination.ingest_orders(
                    body,
                    account_seq=account_seq,
                    raw_ref=replay_raw_id,
                    ts=str(row["ts"]),
                    run_id=replay_run_id,
                )
                snapshots, deltas = destination.ingest_execution_snapshots(
                    body,
                    account_seq=account_seq,
                    raw_ref=replay_raw_id,
                    ts=str(row["ts"]),
                    run_id=replay_run_id,
                )
                counts["execution_snapshots"] += snapshots
                counts["execution_deltas"] += deltas
            elif path == "/api/v1/buying-power":
                counts["buying_power"] += destination.ingest_buying_power(
                    body,
                    account_seq=account_seq,
                    raw_ref=replay_raw_id,
                    ts=str(row["ts"]),
                    run_id=replay_run_id,
                )
            elif path == "/api/v1/commissions":
                counts["commissions"] += destination.ingest_commissions(
                    body,
                    account_seq=account_seq,
                    raw_ref=replay_raw_id,
                    ts=str(row["ts"]),
                    run_id=replay_run_id,
                )
            elif path == "/api/v1/sellable-quantity":
                symbol = str((query.get("symbol") or [""])[0]).strip()
                if not symbol:
                    raise ValueError(f"stored sellable-quantity request has no symbol: {endpoint}")
                counts["sellable"] += destination.ingest_sellable_quantity(
                    body,
                    account_seq=account_seq,
                    raw_ref=replay_raw_id,
                    fallback_symbol=symbol,
                    ts=str(row["ts"]),
                    run_id=replay_run_id,
                )
            else:
                raise ValueError(f"unsupported Foundation broker endpoint in replay: {endpoint}")

            health_channels.add(f"rest:{path}")

        for channel in sorted(health_channels):
            destination.record_source_health(
                source="toss",
                channel=channel,
                source_status="ok",
                run_id=replay_run_id,
            )
        cash_event_rows = destination.post_execution_cash_events(
            account_seq=account_seq,
            run_id=replay_run_id,
        )
        destination.finish_snapshot_run(replay_run_id, account_seq=account_seq)

        return FoundationReplayResult(
            source_run_id=resolved_source_run_id,
            replay_run_id=replay_run_id,
            account_seq=account_seq,
            raw_rows=len(raw_rows),
            account_rows=counts["accounts"],
            holding_rows=counts["holdings"],
            order_rows=counts["orders"],
            buying_power_rows=counts["buying_power"],
            commission_rows=counts["commissions"],
            sellable_quantity_rows=counts["sellable"],
            execution_snapshot_rows=counts["execution_snapshots"],
            execution_delta_rows=counts["execution_deltas"],
            cash_event_rows=cash_event_rows,
        )
    except Exception:
        if destination is not None and destination.current_run_id is not None:
            destination.fail_snapshot_run(destination.current_run_id, "raw replay failed")
        if destination is not None:
            destination.close()
            destination = None
        for replay_file in (
            destination_path,
            Path(f"{destination_path}-wal"),
            Path(f"{destination_path}-shm"),
        ):
            replay_file.unlink(missing_ok=True)
        raise
    finally:
        if destination is not None:
            destination.close()
        source.close()
