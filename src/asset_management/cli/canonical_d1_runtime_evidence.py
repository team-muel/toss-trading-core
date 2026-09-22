"""Bind or replay a persisted canonical D1 source bundle; this never passes Gate D1."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import AssetManagementError, ConfigurationError
from asset_management.time.clock import SystemClock
from asset_management.validation import CanonicalD1RuntimeEvidenceRepository


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True,
                        help="existing canonical SQLite evidence store")
    parser.add_argument("--immutable-store", type=Path, required=True,
                        help="existing immutable data/evidence-store root")
    parser.add_argument("--runtime-run-id", required=True)
    parser.add_argument("--replay-only", action="store_true",
                        help="replay an existing D1 source bundle; do not write one")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _arguments().parse_args(argv)
    if not args.database.is_file():
        print(json.dumps({"status": "BLOCKED", "reason": "CANONICAL_D1_EVIDENCE_DATABASE_MISSING"}, sort_keys=True))
        return 2
    conn = sqlite3.connect(args.database)
    try:
        if conn.execute("SELECT 1 FROM schema_migration WHERE version=21").fetchone() is None:
            raise ConfigurationError("CANONICAL_D1_EVIDENCE_SCHEMA_MISSING")
        repository = CanonicalD1RuntimeEvidenceRepository(conn, SystemClock())
        store = ImmutableDatasetStore(args.immutable_store)
        value = (repository.replay(runtime_run_id=args.runtime_run_id, store=store)
                 if args.replay_only else repository.record(runtime_run_id=args.runtime_run_id, store=store))
    except (AssetManagementError, ValueError, sqlite3.DatabaseError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 2
    finally:
        conn.close()
    print(json.dumps({"status": "EVIDENCE_REPLAYED" if args.replay_only else "EVIDENCE_RECORDED",
                      "runtime_run_id": value.runtime_run_id,
                      "canonical_evidence_hash": value.content_hash,
                      "catalog_object_id": value.catalog_object_id,
                      "model_registry_snapshot_id": value.model_registry_snapshot_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
