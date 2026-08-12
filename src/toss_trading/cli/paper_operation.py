from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from toss_trading.paper import load_latest_raw_prices, run_paper_operation
from toss_trading.research.costs import load_execution_cost_model


def _json_object(path: str | Path, *, name: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _atomic_json(path: str | Path, payload: dict) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")
    handle = tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one persistent, local-only paper operation from a published artifact."
    )
    parser.add_argument("--strategy-artifact", required=True)
    parser.add_argument("--source-summary", required=True)
    parser.add_argument("--lake-root", required=True)
    parser.add_argument("--paper-db", required=True)
    parser.add_argument("--planner-ledger", required=True)
    parser.add_argument("--cost-calibration", required=True)
    parser.add_argument("--policy", default="config/default_policy.yaml")
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact = _json_object(args.strategy_artifact, name="strategy artifact")
    summary = _json_object(args.source_summary, name="source summary")
    data_progress = summary.get("data_progress")
    if not isinstance(data_progress, dict):
        raise ValueError("source summary is missing data progress")
    through_date = str(data_progress.get("complete_through_date") or "")
    rebalance_rows = artifact.get("rebalances")
    if not isinstance(rebalance_rows, list) or not rebalance_rows:
        raise ValueError("strategy artifact has no published rebalance")
    target = rebalance_rows[-1].get("target_weights")
    if not isinstance(target, dict):
        raise ValueError("latest strategy rebalance has no target weights")
    prices = load_latest_raw_prices(
        args.lake_root,
        through_date=through_date,
        symbols={str(symbol).strip().upper() for symbol in target},
    )
    # Existing paper positions can require prices even after leaving a target.
    if Path(args.paper_db).exists():
        import sqlite3

        connection = sqlite3.connect(args.paper_db)
        try:
            rows = connection.execute(
                "SELECT symbol FROM paper_position "
                "WHERE CAST(quantity_decimal AS NUMERIC) != 0"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            connection.close()
        missing = {str(row[0]) for row in rows} - set(prices)
        if missing:
            prices.update(
                load_latest_raw_prices(
                    args.lake_root,
                    through_date=through_date,
                    symbols=missing,
                )
            )
    cost_model = load_execution_cost_model(
        args.cost_calibration,
        as_of=through_date,
    )
    report = run_paper_operation(
        artifact=artifact,
        source_summary=summary,
        prices=prices,
        paper_db_path=args.paper_db,
        planner_ledger_path=args.planner_ledger,
        cost_model=cost_model,
        policy_path=args.policy,
    )
    _atomic_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
