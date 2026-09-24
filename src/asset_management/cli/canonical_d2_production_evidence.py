"""Bind or replay one real canonical D2 production-evidence bundle.

This command has no Gate D2 PASS mode.  It exits blocked if any required
persisted artifact is absent, stale, ambiguous, or cannot be replayed.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import AssetManagementError, ConfigurationError
from asset_management.time.clock import SystemClock
from asset_management.validation import CanonicalD2ProductionEvidenceRepository


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True,
                        help="existing production SQLite evidence store")
    parser.add_argument("--immutable-store", type=Path, required=True,
                        help="existing immutable FRED/ALFRED object-store root")
    parser.add_argument("--runtime-run-id", required=True)
    parser.add_argument("--replay-only", action="store_true",
                        help="replay an existing canonical bundle; do not write a new one")
    return parser


def _result(value, *, operation_mode: str, runtime_lineage: dict[str, str], observed_at: str) -> dict[str, object]:
    result = {
        "status": "EVIDENCE_REPLAYED" if operation_mode == "REPLAY_ONLY" else "EVIDENCE_RECORDED",
        "operation_mode": operation_mode,
        "runtime_run_id": value.runtime_run_id,
        "canonical_evidence_hash": value.content_hash,
        "model_registry_snapshot_id": value.model_registry_snapshot_id,
        "model_registry_binding_hash": value.model_registry_binding_hash,
        "factor_risk_calculation_id": value.factor_risk_calculation_id,
        "risk_free_manifest_id": value.risk_free_manifest_id,
        "risk_free_curve_hash": value.risk_free_curve_hash,
        "accounting_snapshot_id": value.accounting_snapshot_id,
        "runtime_lineage": runtime_lineage,
        "observed_at": observed_at,
    }
    canonical_result = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["operation_receipt_sha256"] = sha256(canonical_result.encode("utf-8")).hexdigest()
    return result


def main(argv: list[str] | None = None) -> int:
    args = _arguments().parse_args(argv)
    if not args.database.is_file():
        print(json.dumps({"status": "BLOCKED", "reason": "CANONICAL_D2_EVIDENCE_DATABASE_MISSING"}, sort_keys=True))
        return 2
    conn = sqlite3.connect(args.database)
    try:
        migrated = conn.execute("SELECT 1 FROM schema_migration WHERE version=19").fetchone()
        if migrated is None:
            raise ConfigurationError("CANONICAL_D2_EVIDENCE_SCHEMA_MISSING")
        repository = CanonicalD2ProductionEvidenceRepository(conn, SystemClock())
        store = ImmutableDatasetStore(args.immutable_store)
        value = (repository.replay(runtime_run_id=args.runtime_run_id, store=store)
                 if args.replay_only else repository.record(
                     runtime_run_id=args.runtime_run_id, store=store))
        runtime_lineage = {
            "runtime_run_id": value.runtime_run_id,
            "as_of_utc": value.as_of_utc,
            "information_cutoff_utc": value.information_cutoff_utc,
            "code_revision": value.code_revision,
        }
        result = _result(
            value,
            operation_mode="REPLAY_ONLY" if args.replay_only else "RECORD",
            runtime_lineage=runtime_lineage,
            observed_at=SystemClock().now_utc().isoformat(),
        )
    except (AssetManagementError, ValueError, sqlite3.DatabaseError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 2
    finally:
        conn.close()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
