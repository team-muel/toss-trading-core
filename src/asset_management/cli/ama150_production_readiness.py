"""Read-only AMA-150 production evidence readiness inspector."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from asset_management.validation.forecast_production_readiness import evaluate_production_readiness


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="existing immutable SQLite evidence database")
    parser.add_argument("--immutable-store", type=Path, help="existing research data-lake root")
    parser.add_argument("--runtime-run-id", help="existing runtime run to inspect")
    return parser


def _read_only_connection(path: Path | None) -> sqlite3.Connection | None:
    if path is None or not path.is_file():
        return None
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def main(argv: list[str] | None = None) -> int:
    args = _arguments().parse_args(argv)
    database_reason = None
    try:
        connection = _read_only_connection(args.database)
    except (OSError, sqlite3.DatabaseError):
        connection = None
        database_reason = "EVIDENCE_DATABASE_UNREADABLE"
    try:
        try:
            report = evaluate_production_readiness(
                connection=connection, immutable_store=args.immutable_store,
                runtime_run_id=args.runtime_run_id, database_reason=database_reason)
        except sqlite3.DatabaseError:
            # Opening a SQLite URI is lazy: a corrupt file can fail only when
            # the first schema read occurs.  Normalize it to the same
            # structured fail-closed result rather than exposing a traceback.
            report = evaluate_production_readiness(
                connection=None, immutable_store=args.immutable_store,
                runtime_run_id=args.runtime_run_id,
                database_reason="EVIDENCE_DATABASE_UNREADABLE")
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_SAMPLE_INGEST" else 2


if __name__ == "__main__":
    raise SystemExit(main())
