"""Canonical persisted-evidence bundle for a future Gate D2 evaluation.

This module deliberately does *not* evaluate Gate D2 and contains no PASS
flag.  It can only bind and replay evidence already persisted by the AMA-38,
AMA-46, FRED/ALFRED, and AMA-9/10 boundaries for one existing runtime run.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError, InvariantViolation, ReconciliationError
from asset_management.governance import RuntimeModelRegistryEvidenceRepository
from asset_management.ledger import ProviderAccountingSnapshotRepository
from asset_management.pricing import materialize_usd_fred_risk_free_curve
from asset_management.risk import FactorRiskCalculationRepository
from asset_management.time.clock import Clock


_HASH = re.compile(r"[0-9a-f]{64}")
_HORIZONS = (21, 63, 126, 252)


def _utc(value: object, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


def _stored_utc(value: object, reason: str) -> datetime:
    try:
        return _utc(datetime.fromisoformat(str(value)), reason)
    except (TypeError, ValueError) as exc:
        raise InvariantViolation(reason) from exc


def _hash(value: object, reason: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise InvariantViolation(reason)
    return value


@dataclass(frozen=True, slots=True)
class CanonicalD2ProductionEvidence:
    """Content-addressed evidence binding; it is never an acceptance decision."""

    runtime_run_id: str
    model_registry_snapshot_id: str
    model_registry_binding_hash: str
    factor_risk_calculation_id: str
    risk_free_manifest_id: str
    risk_free_curve_hash: str
    accounting_snapshot_id: str
    content_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_run_id, str) or not self.runtime_run_id.strip():
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_INVALID")
        if (not isinstance(self.model_registry_snapshot_id, str) or
                not self.model_registry_snapshot_id.strip() or
                not isinstance(self.accounting_snapshot_id, str) or
                not self.accounting_snapshot_id.strip()):
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_INVALID")
        for value in (self.model_registry_binding_hash, self.factor_risk_calculation_id,
                      self.risk_free_manifest_id, self.risk_free_curve_hash, self.content_hash):
            _hash(value, "CANONICAL_D2_EVIDENCE_INVALID")


class CanonicalD2ProductionEvidenceRepository:
    """Bind persisted production evidence, rejecting absence and ambiguity.

    The only caller-selected input is a content-addressed FRED/ALFRED manifest
    identifier.  It is verified against both the immutable object store and the
    runtime's persisted ingestion record.  All other identifiers are selected
    from the runtime evidence store and must be unique.
    """

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        if not isinstance(conn, sqlite3.Connection) or not hasattr(clock, "now_utc"):
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_REPOSITORY_INVALID")
        self._conn = conn
        self._clock = clock
        self._conn.execute("PRAGMA foreign_keys=ON")

    def record(self, *, runtime_run_id: str, risk_free_manifest_id: str,
               store: ImmutableDatasetStore) -> CanonicalD2ProductionEvidence:
        """Persist one bundle only after every source can be independently replayed."""
        result, body = self._assemble(runtime_run_id=runtime_run_id,
                                      risk_free_manifest_id=risk_free_manifest_id, store=store)
        recorded_at = _utc(self._clock.now_utc(), "CANONICAL_D2_EVIDENCE_TIME_INVALID")
        payload = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        existing = self._conn.execute(
            """SELECT model_registry_snapshot_id, model_registry_binding_hash,
                      factor_risk_calculation_id, risk_free_manifest_id, risk_free_curve_hash,
                      accounting_snapshot_id, payload_json, content_hash
               FROM am_canonical_d2_production_evidence WHERE runtime_run_id=?""",
            (result.runtime_run_id,),
        ).fetchone()
        if existing is not None:
            expected = (result.model_registry_snapshot_id, result.model_registry_binding_hash,
                        result.factor_risk_calculation_id, result.risk_free_manifest_id,
                        result.risk_free_curve_hash, result.accounting_snapshot_id,
                        payload, result.content_hash)
            if tuple(existing) != expected:
                raise InvariantViolation("CANONICAL_D2_EVIDENCE_CONFLICT")
            return result
        with self._conn:
            self._conn.execute(
                """INSERT INTO am_canonical_d2_production_evidence
                   (runtime_run_id, model_registry_snapshot_id, model_registry_binding_hash,
                    factor_risk_calculation_id, risk_free_manifest_id, risk_free_curve_hash,
                    accounting_snapshot_id, payload_json, content_hash, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (result.runtime_run_id, result.model_registry_snapshot_id,
                 result.model_registry_binding_hash, result.factor_risk_calculation_id,
                 result.risk_free_manifest_id, result.risk_free_curve_hash,
                 result.accounting_snapshot_id, payload, result.content_hash,
                 recorded_at.isoformat()),
            )
        return result

    def replay(self, *, runtime_run_id: str, store: ImmutableDatasetStore) -> CanonicalD2ProductionEvidence:
        """Recompute every component from persisted evidence and compare its hash."""
        if not isinstance(runtime_run_id, str) or not runtime_run_id.strip():
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_INVALID")
        row = self._conn.execute(
            """SELECT risk_free_manifest_id, payload_json, content_hash
               FROM am_canonical_d2_production_evidence WHERE runtime_run_id=?""",
            (runtime_run_id,),
        ).fetchone()
        if row is None:
            raise DataQualityError("CANONICAL_D2_EVIDENCE_MISSING")
        try:
            stored = json.loads(str(row[1]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_INVALID") from exc
        if not isinstance(stored, dict) or digest(canonical(stored)) != row[2]:
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_INVALID")
        result, body = self._assemble(runtime_run_id=runtime_run_id,
                                      risk_free_manifest_id=str(row[0]), store=store)
        if body != stored or result.content_hash != row[2]:
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_REPLAY_MISMATCH")
        return result

    def _assemble(self, *, runtime_run_id: str, risk_free_manifest_id: str,
                  store: ImmutableDatasetStore) -> tuple[CanonicalD2ProductionEvidence, dict[str, object]]:
        if (not isinstance(runtime_run_id, str) or not runtime_run_id.strip() or
                not isinstance(risk_free_manifest_id, str) or
                _HASH.fullmatch(risk_free_manifest_id) is None or
                not isinstance(store, ImmutableDatasetStore)):
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_INVALID")
        runtime = self._conn.execute(
            """SELECT as_of_utc, information_cutoff_utc, code_revision
               FROM am_runtime_run WHERE runtime_run_id=?""", (runtime_run_id,)).fetchone()
        if runtime is None:
            raise DataQualityError("CANONICAL_D2_RUNTIME_MISSING")
        as_of = _stored_utc(runtime[0], "CANONICAL_D2_RUNTIME_INVALID")
        cutoff = _stored_utc(runtime[1], "CANONICAL_D2_RUNTIME_INVALID")
        if cutoff > as_of or not isinstance(runtime[2], str) or not runtime[2].strip():
            raise InvariantViolation("CANONICAL_D2_RUNTIME_INVALID")

        snapshot_id, binding_hash = self._model_binding(runtime_run_id)
        factor = FactorRiskCalculationRepository(self._conn, self._clock).replay(
            self._sole_id("am_factor_risk_calculation", "factor_risk_calculation_id", runtime_run_id,
                          "CANONICAL_D2_FACTOR_RISK_MISSING_OR_AMBIGUOUS"),
            model_registry_evidence=RuntimeModelRegistryEvidenceRepository(self._conn, self._clock),
        )
        if factor.runtime_run_id != runtime_run_id:
            raise InvariantViolation("CANONICAL_D2_FACTOR_RISK_INVALID")
        accounting_id = self._sole_id("am_provider_accounting_snapshot", "accounting_snapshot_id",
                                      runtime_run_id, "CANONICAL_D2_ACCOUNTING_MISSING_OR_AMBIGUOUS")
        try:
            accounting = ProviderAccountingSnapshotRepository(self._conn).replay(accounting_id)
        except ReconciliationError as exc:
            raise DataQualityError("CANONICAL_D2_ACCOUNTING_UNVERIFIED") from exc
        if accounting.runtime_run_id != runtime_run_id:
            raise InvariantViolation("CANONICAL_D2_ACCOUNTING_INVALID")

        curve = materialize_usd_fred_risk_free_curve(
            store=store, manifest_id=risk_free_manifest_id, information_cutoff=cutoff)
        manifest, _ = store.read(risk_free_manifest_id)
        manifest_row = self._conn.execute(
            """SELECT ingestion.provider, manifest.layer, manifest.dataset_name,
                      manifest.content_hash, manifest.received_at_utc
               FROM am_dataset_manifest manifest
               JOIN am_ingestion_run ingestion USING(ingestion_run_id)
               WHERE manifest.dataset_manifest_id=? AND ingestion.runtime_run_id=?""",
            (risk_free_manifest_id, runtime_run_id),
        ).fetchone()
        if (manifest_row is None or manifest_row[0] != "fred-alfred" or manifest_row[1] != "bronze" or
                manifest_row[2] != "risk-free-curve" or manifest_row[3] != manifest.content_sha256 or
                _stored_utc(manifest_row[4], "CANONICAL_D2_RISK_FREE_INVALID") > cutoff):
            raise DataQualityError("CANONICAL_D2_RISK_FREE_UNVERIFIED")
        returns = [curve.return_for(currency="USD", horizon=horizon, information_cutoff=cutoff).payload()
                   for horizon in _HORIZONS]
        curve_hash = digest(canonical(returns))
        body = {
            "schema_version": "canonical-d2-production-evidence@1",
            "runtime": {"runtime_run_id": runtime_run_id, "as_of": as_of.isoformat(),
                        "information_cutoff": cutoff.isoformat(), "code_revision": runtime[2]},
            "model_registry": {"snapshot_id": snapshot_id, "runtime_binding_hash": binding_hash},
            "factor_risk": {"calculation_id": factor.factor_risk_calculation_id,
                            "content_hash": factor.content_hash},
            "risk_free": {"manifest_id": risk_free_manifest_id,
                          "manifest_content_hash": manifest.content_sha256,
                          "curve": returns, "curve_hash": curve_hash},
            "provider_accounting": {"snapshot_id": accounting.accounting_snapshot_id,
                                    "content_hash": accounting.content_hash},
        }
        content_hash = digest(canonical(body))
        return CanonicalD2ProductionEvidence(
            runtime_run_id, snapshot_id, binding_hash, factor.factor_risk_calculation_id,
            risk_free_manifest_id, curve_hash, accounting.accounting_snapshot_id, content_hash), body

    def _model_binding(self, runtime_run_id: str) -> tuple[str, str]:
        row = self._conn.execute(
            """SELECT model_registry_snapshot_id, content_hash
               FROM am_runtime_model_registry WHERE runtime_run_id=?""", (runtime_run_id,)
        ).fetchone()
        if row is None or not isinstance(row[0], str) or not row[0].strip():
            raise DataQualityError("CANONICAL_D2_MODEL_REGISTRY_MISSING")
        return str(row[0]), _hash(row[1], "CANONICAL_D2_MODEL_REGISTRY_INVALID")

    def _sole_id(self, table: str, column: str, runtime_run_id: str, reason: str) -> str:
        # Table and column are module constants; values are still fetched through
        # parameters so a caller cannot influence a query or pick an artifact.
        rows = self._conn.execute(
            f"SELECT {column} FROM {table} WHERE runtime_run_id=? ORDER BY {column}",
            (runtime_run_id,),
        ).fetchall()
        if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0].strip():
            raise DataQualityError(reason)
        return str(rows[0][0])
