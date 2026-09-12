"""Canonical persisted-evidence bundle for a future Gate D2 evaluation.

This module deliberately does *not* evaluate Gate D2 and contains no PASS
flag.  It can only bind and replay evidence already persisted by the AMA-38,
AMA-46, FRED/ALFRED, and AMA-9/10 boundaries for one existing runtime run.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
from collections.abc import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.domain.errors import DataQualityError, InvariantViolation, ReconciliationError
from asset_management.governance import RuntimeModelRegistryEvidenceRepository
from asset_management.ledger import ProviderAccountingSnapshotRepository
from asset_management.pricing import materialize_usd_fred_risk_free_curve
from asset_management.risk import FactorRiskCalculationRepository
from asset_management.time.clock import Clock

from .external_attestation import (
    RuntimeAttestorRegistry,
    require_external_evidence_attestation,
    require_runtime_attestor_registry,
)


_HASH = re.compile(r"[0-9a-f]{64}")
_HORIZONS = (21, 63, 126, 252)
_FRED_SERIES = {21: "DGS1MO", 63: "DGS3MO", 126: "DGS6MO", 252: "DGS1"}
_FRED_FRESHNESS_POLICY_ID = "fred-usd-risk-free-freshness@1"
_FRED_MAXIMUM_AGE_SECONDS = 259200
_FRED_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
_GOVERNANCE_SOURCE = "model-governance"
_GOVERNANCE_ENDPOINT = "/v1/model-governance/reviews"
_GOVERNANCE_SCHEMA = "model-governance-review@1"
_ESTIMATOR_SOURCE = "factor-risk-provider"
_ESTIMATOR_ENDPOINT = "/v1/factor-risk/estimator-inputs"
_ESTIMATOR_SCHEMA = "factor-risk-estimator-inputs@1"


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

    Every input is selected from one existing runtime-bound provenance record.
    This boundary has no API for registering raw responses, policies, manifests,
    reviews, or estimator inputs; producers must establish those independently.
    """

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        if not isinstance(conn, sqlite3.Connection) or not hasattr(clock, "now_utc"):
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_REPOSITORY_INVALID")
        self._conn = conn
        self._clock = clock
        self._conn.execute("PRAGMA foreign_keys=ON")

    def record(self, *, runtime_run_id: str,
               store: ImmutableDatasetStore) -> CanonicalD2ProductionEvidence:
        """Persist one bundle only after every source can be independently replayed."""
        result, body = self._assemble(runtime_run_id=runtime_run_id, store=store)
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
        result, body = self._assemble(runtime_run_id=runtime_run_id, store=store)
        if (result.risk_free_manifest_id != row[0] or body != stored or
                result.content_hash != row[2]):
            raise InvariantViolation("CANONICAL_D2_EVIDENCE_REPLAY_MISMATCH")
        return result

    def _assemble(self, *, runtime_run_id: str,
                  store: ImmutableDatasetStore) -> tuple[CanonicalD2ProductionEvidence, dict[str, object]]:
        if (not isinstance(runtime_run_id, str) or not runtime_run_id.strip() or
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
        attestor_registry = require_runtime_attestor_registry(
            conn=self._conn, runtime_run_id=runtime_run_id, cutoff=cutoff)

        snapshot_id, binding_hash = self._model_binding(runtime_run_id)
        self._require_model_review_raw_provenance(
            snapshot_id, cutoff=cutoff, registry=attestor_registry)
        factor = FactorRiskCalculationRepository(self._conn, self._clock).replay(
            self._sole_id("am_factor_risk_calculation", "factor_risk_calculation_id", runtime_run_id,
                          "CANONICAL_D2_FACTOR_RISK_MISSING_OR_AMBIGUOUS"),
            model_registry_evidence=RuntimeModelRegistryEvidenceRepository(self._conn, self._clock),
        )
        if factor.runtime_run_id != runtime_run_id:
            raise InvariantViolation("CANONICAL_D2_FACTOR_RISK_INVALID")
        self._require_estimator_raw_provenance(
            runtime_run_id=runtime_run_id,
            factor_risk_calculation_id=factor.factor_risk_calculation_id,
            cutoff=cutoff,
            store=store,
            registry=attestor_registry,
        )
        accounting_id = self._sole_id("am_provider_accounting_snapshot", "accounting_snapshot_id",
                                      runtime_run_id, "CANONICAL_D2_ACCOUNTING_MISSING_OR_AMBIGUOUS")
        try:
            accounting = ProviderAccountingSnapshotRepository(self._conn).replay(accounting_id)
        except ReconciliationError as exc:
            raise DataQualityError("CANONICAL_D2_ACCOUNTING_UNVERIFIED") from exc
        if accounting.runtime_run_id != runtime_run_id:
            raise InvariantViolation("CANONICAL_D2_ACCOUNTING_INVALID")
        accounting_provenance = self._require_provider_accounting_raw_provenance(
            accounting_snapshot_id=accounting_id, runtime_run_id=runtime_run_id,
            cutoff=cutoff, registry=attestor_registry)

        risk_free_manifest_id, policy_maximum_age = self._risk_free_artifact(runtime_run_id, cutoff=cutoff)
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
        self._require_fred_raw_provenance(runtime_run_id=runtime_run_id, curve=curve,
                                          cutoff=cutoff, maximum_age_seconds=policy_maximum_age,
                                          registry=attestor_registry)
        returns = [curve.return_for(currency="USD", horizon=horizon, information_cutoff=cutoff).payload()
                   for horizon in _HORIZONS]
        curve_hash = digest(canonical(returns))
        body = {
            "schema_version": "canonical-d2-production-evidence@1",
            "runtime": {"runtime_run_id": runtime_run_id, "as_of": as_of.isoformat(),
                        "information_cutoff": cutoff.isoformat(), "code_revision": runtime[2]},
            "model_registry": {"snapshot_id": snapshot_id, "runtime_binding_hash": binding_hash},
            "external_attestor_registry": {"snapshot_id": attestor_registry.snapshot_id,
                                            "content_hash": attestor_registry.content_hash},
            "factor_risk": {"calculation_id": factor.factor_risk_calculation_id,
                            "content_hash": factor.content_hash},
            "risk_free": {"manifest_id": risk_free_manifest_id,
                          "manifest_content_hash": manifest.content_sha256,
                          "curve": returns, "curve_hash": curve_hash},
            "provider_accounting": {"snapshot_id": accounting.accounting_snapshot_id,
                                    "content_hash": accounting.content_hash,
                                    "provenance": accounting_provenance},
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

    def _risk_free_artifact(self, runtime_run_id: str, *, cutoff: datetime) -> tuple[str, int]:
        row = self._conn.execute(
            """SELECT artifact.risk_free_manifest_id, artifact.content_hash,
                      policy.fred_risk_free_freshness_policy_id,
                      policy.maximum_observation_age_seconds, policy.content_hash, policy.published_at_utc,
                      artifact.recorded_at_utc
               FROM am_runtime_fred_risk_free_artifact artifact
               JOIN am_fred_risk_free_freshness_policy policy
                 ON policy.fred_risk_free_freshness_policy_id=artifact.fred_risk_free_freshness_policy_id
               WHERE artifact.runtime_run_id=?""", (runtime_run_id,)
        ).fetchone()
        if row is None:
            raise DataQualityError("CANONICAL_D2_RISK_FREE_PROVENANCE_MISSING")
        manifest_id, artifact_hash, policy_id, maximum_age, policy_hash, published_at, recorded_at = row
        if (not isinstance(manifest_id, str) or _HASH.fullmatch(manifest_id) is None or
                policy_id != _FRED_FRESHNESS_POLICY_ID or type(maximum_age) is not int or
                maximum_age <= 0 or maximum_age > _FRED_MAXIMUM_AGE_SECONDS or
                _stored_utc(published_at, "CANONICAL_D2_RISK_FREE_POLICY_INVALID") > cutoff or
                _stored_utc(recorded_at, "CANONICAL_D2_RISK_FREE_PROVENANCE_INVALID") > cutoff or
                _hash(policy_hash, "CANONICAL_D2_RISK_FREE_POLICY_INVALID") != digest(canonical({
                    "schema_version": "fred-risk-free-freshness-policy@1",
                    "fred_risk_free_freshness_policy_id": policy_id,
                    "maximum_observation_age_seconds": maximum_age,
                }))):
            raise InvariantViolation("CANONICAL_D2_RISK_FREE_POLICY_INVALID")
        raw_rows = self._conn.execute(
            """SELECT provenance.series_id, provenance.raw_response_id, raw.response_hash
               FROM am_runtime_fred_risk_free_raw_provenance provenance
               JOIN am_raw_api_response raw ON raw.raw_response_id=provenance.raw_response_id
               WHERE provenance.runtime_run_id=? ORDER BY provenance.series_id""", (runtime_run_id,)
        ).fetchall()
        raw = [{"series_id": str(item[0]), "raw_response_id": str(item[1]),
                "response_hash": str(item[2])} for item in raw_rows]
        body = {"schema_version": "runtime-fred-risk-free-artifact@1",
                "runtime_run_id": runtime_run_id, "risk_free_manifest_id": manifest_id,
                "fred_risk_free_freshness_policy_id": policy_id, "raw_responses": raw}
        if (set(item["series_id"] for item in raw) != set(_FRED_SERIES.values()) or
                len(raw) != len(_FRED_SERIES) or _hash(artifact_hash,
                "CANONICAL_D2_RISK_FREE_PROVENANCE_INVALID") != digest(canonical(body))):
            raise InvariantViolation("CANONICAL_D2_RISK_FREE_PROVENANCE_INVALID")
        return manifest_id, maximum_age

    def _require_fred_raw_provenance(self, *, runtime_run_id: str, curve, cutoff: datetime,
                                     maximum_age_seconds: int,
                                     registry: RuntimeAttestorRegistry) -> None:
        rows = self._conn.execute(
            """SELECT provenance.series_id, provenance.raw_response_id
               FROM am_runtime_fred_risk_free_raw_provenance provenance
               WHERE provenance.runtime_run_id=?""", (runtime_run_id,)
        ).fetchall()
        raw_by_series = {str(series): str(raw_id) for series, raw_id in rows}
        if set(raw_by_series) != set(_FRED_SERIES.values()) or len(rows) != len(raw_by_series):
            raise DataQualityError("CANONICAL_D2_RISK_FREE_PROVENANCE_MISSING")
        raw_store = SQLiteRawResponseStore(self._conn)
        for horizon, series_id in _FRED_SERIES.items():
            point = curve.points[horizon]
            try:
                response = raw_store.verified(raw_by_series[series_id])
            except (KeyError, TypeError, ValueError) as exc:
                raise DataQualityError("CANONICAL_D2_RISK_FREE_RAW_UNVERIFIED") from exc
            if (response.source != "fred-alfred" or response.endpoint != self._fred_endpoint(series_id) or
                    response.http_method != "GET" or response.status_code != 200 or
                    response.schema_version != "fred-observations-output-type-3-v1" or
                    response.received_at.astimezone(timezone.utc) > cutoff or
                    point.available_at != response.received_at.astimezone(timezone.utc) or
                    cutoff - point.as_of > timedelta(seconds=maximum_age_seconds) or
                    not self._fred_raw_matches(response.body, series_id, point)):
                raise DataQualityError("CANONICAL_D2_RISK_FREE_RAW_UNVERIFIED")
            require_external_evidence_attestation(
                conn=self._conn, raw_response_id=raw_by_series[series_id], response=response,
                cutoff=cutoff, registry=registry)

    @staticmethod
    def _fred_raw_matches(body: object, series_id: str, point: object) -> bool:
        if not isinstance(body, Mapping) or not isinstance(body.get("observations"), list):
            return False
        expected_date = point.as_of.date().isoformat()
        expected_value = str(point.annualized_rate * 100)
        matches = [row for row in body["observations"] if isinstance(row, Mapping) and
                   row.get("date") == expected_date and row.get("value") == expected_value]
        return len(matches) == 1 and series_id in _FRED_SERIES.values()

    @staticmethod
    def _fred_endpoint(series_id: str) -> str:
        return f"{_FRED_ENDPOINT}?series_id={series_id}&output_type=3"

    def _require_model_review_raw_provenance(self, snapshot_id: str, *, cutoff: datetime,
                                             registry: RuntimeAttestorRegistry) -> None:
        row = self._conn.execute(
            "SELECT registry_hash, payload_json, review_evidence_json "
            "FROM am_model_registry_snapshot WHERE model_registry_snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        try:
            registry_payload = json.loads(str(row[1])) if row is not None else None
            reviews = json.loads(str(row[2])) if row is not None else None
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvariantViolation("CANONICAL_D2_MODEL_REVIEW_UNVERIFIED") from exc
        if (not isinstance(registry_payload, Mapping) or not isinstance(reviews, list) or
                row[0] != snapshot_id or registry_payload.get("registry_hash") != snapshot_id or
                digest(canonical({"models": registry_payload.get("models"),
                                  "transitions": registry_payload.get("transitions")})) != snapshot_id):
            raise InvariantViolation("CANONICAL_D2_MODEL_REVIEW_UNVERIFIED")
        raw_store = SQLiteRawResponseStore(self._conn)
        for review in reviews:
            if not isinstance(review, Mapping) or set(review) != {"evidence_id", "content_hash", "recorded_at"}:
                raise InvariantViolation("CANONICAL_D2_MODEL_REVIEW_UNVERIFIED")
            review_hash = _hash(review["content_hash"], "CANONICAL_D2_MODEL_REVIEW_UNVERIFIED")
            row = self._conn.execute(
                """SELECT evidence.payload_json, provenance.raw_response_id, provenance.content_hash,
                          raw.response_hash, provenance.recorded_at_utc
                   FROM am_model_governance_review_evidence evidence
                   JOIN am_model_governance_review_raw_provenance provenance
                     ON provenance.review_evidence_id=evidence.review_evidence_id
                   JOIN am_raw_api_response raw ON raw.raw_response_id=provenance.raw_response_id
                   WHERE evidence.review_evidence_id=?""", (review_hash,)
            ).fetchone()
            if row is None:
                raise DataQualityError("CANONICAL_D2_MODEL_REVIEW_PROVENANCE_MISSING")
            try:
                payload = json.loads(str(row[0]))
                response = raw_store.verified(str(row[1]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise InvariantViolation("CANONICAL_D2_MODEL_REVIEW_UNVERIFIED") from exc
            provenance_body = {"schema_version": "model-governance-review-raw-provenance@1",
                               "review_evidence_id": review_hash, "raw_response_id": str(row[1]),
                               "raw_response_hash": str(row[3])}
            expected_raw = {"evidence_id": review["evidence_id"], "model_key": payload.get("model_key"),
                            "from_status": payload.get("from_status"), "to_status": payload.get("to_status"),
                            "owner": payload.get("owner"), "decision": "APPROVED",
                            "reviewed_at": payload.get("recorded_at"),
                            "model_registry_snapshot_id": snapshot_id}
            if (not isinstance(payload, Mapping) or payload.get("evidence") != {
                    "source_response_id": str(row[1]), "decision": "APPROVED",
                    "reviewed_at": payload.get("recorded_at")} or
                    _hash(row[2], "CANONICAL_D2_MODEL_REVIEW_UNVERIFIED") != digest(canonical(provenance_body)) or
                    response.source != _GOVERNANCE_SOURCE or response.endpoint != _GOVERNANCE_ENDPOINT or
                    response.http_method != "GET" or response.status_code != 200 or
                    response.schema_version != _GOVERNANCE_SCHEMA or
                    response.received_at.astimezone(timezone.utc) > cutoff or
                    _stored_utc(row[4], "CANONICAL_D2_MODEL_REVIEW_UNVERIFIED") > cutoff or
                    response.body != expected_raw):
                raise InvariantViolation("CANONICAL_D2_MODEL_REVIEW_UNVERIFIED")
            require_external_evidence_attestation(
                conn=self._conn, raw_response_id=str(row[1]), response=response, cutoff=cutoff,
                registry=registry)

    def _require_estimator_raw_provenance(self, *, runtime_run_id: str,
                                          factor_risk_calculation_id: str,
                                          cutoff: datetime,
                                          store: ImmutableDatasetStore,
                                          registry: RuntimeAttestorRegistry) -> None:
        row = self._conn.execute(
            """SELECT estimator.factor_risk_estimator_evidence_id, estimator.payload_json,
                      provenance.source_manifest_id, provenance.raw_response_id,
                      provenance.content_hash, raw.response_hash, ingestion.provider,
                      manifest.layer, manifest.dataset_name, manifest.content_hash,
                      manifest.received_at_utc, provenance.recorded_at_utc
               FROM am_factor_risk_calculation calculation
               JOIN am_factor_risk_estimator_evidence estimator
                 ON estimator.factor_risk_estimator_evidence_id=calculation.factor_risk_estimator_evidence_id
               JOIN am_factor_risk_estimator_raw_provenance provenance
                 ON provenance.factor_risk_estimator_evidence_id=estimator.factor_risk_estimator_evidence_id
               JOIN am_raw_api_response raw ON raw.raw_response_id=provenance.raw_response_id
               JOIN am_dataset_manifest manifest ON manifest.dataset_manifest_id=provenance.source_manifest_id
               JOIN am_ingestion_run ingestion ON ingestion.ingestion_run_id=manifest.ingestion_run_id
               WHERE calculation.factor_risk_calculation_id=? AND calculation.runtime_run_id=?
                 AND estimator.runtime_run_id=? AND ingestion.runtime_run_id=?""",
            (factor_risk_calculation_id, runtime_run_id, runtime_run_id, runtime_run_id),
        ).fetchone()
        if row is None:
            raise DataQualityError("CANONICAL_D2_ESTIMATOR_PROVENANCE_MISSING")
        try:
            estimator = json.loads(str(row[1]))
            response = SQLiteRawResponseStore(self._conn).verified(str(row[3]))
            manifest, body = store.read(str(row[2]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvariantViolation("CANONICAL_D2_ESTIMATOR_PROVENANCE_UNVERIFIED") from exc
        provenance_body = {"schema_version": "factor-risk-estimator-raw-provenance@1",
                           "factor_risk_estimator_evidence_id": str(row[0]),
                           "source_manifest_id": str(row[2]), "raw_response_id": str(row[3]),
                           "raw_response_hash": str(row[5])}
        expected = {"estimator": estimator}
        if (not isinstance(estimator, Mapping) or response.source != _ESTIMATOR_SOURCE or
                response.endpoint != _ESTIMATOR_ENDPOINT or response.http_method != "GET" or
                response.status_code != 200 or response.schema_version != _ESTIMATOR_SCHEMA or
                response.received_at.astimezone(timezone.utc) > cutoff or
                _stored_utc(row[10], "CANONICAL_D2_ESTIMATOR_PROVENANCE_UNVERIFIED") > cutoff or
                _stored_utc(row[11], "CANONICAL_D2_ESTIMATOR_PROVENANCE_UNVERIFIED") > cutoff or
                response.body != expected or body != expected or manifest.manifest_id != row[2] or
                manifest.content_sha256 != row[9] or manifest.content_sha256 != row[5] or
                manifest.layer != "bronze" or manifest.source != _ESTIMATOR_SOURCE or
                manifest.dataset != "factor-risk-estimator-inputs" or row[6] != _ESTIMATOR_SOURCE or
                row[7] != "bronze" or row[8] != "factor-risk-estimator-inputs" or
                _stored_utc(manifest.available_at, "CANONICAL_D2_ESTIMATOR_PROVENANCE_UNVERIFIED") > cutoff or
                _hash(row[4], "CANONICAL_D2_ESTIMATOR_PROVENANCE_UNVERIFIED") != digest(canonical(provenance_body))):
            raise InvariantViolation("CANONICAL_D2_ESTIMATOR_PROVENANCE_UNVERIFIED")
        require_external_evidence_attestation(
            conn=self._conn, raw_response_id=str(row[3]), response=response, cutoff=cutoff,
            registry=registry)

    def _require_provider_accounting_raw_provenance(self, *, accounting_snapshot_id: str,
                                                    runtime_run_id: str, cutoff: datetime,
                                                    registry: RuntimeAttestorRegistry) -> dict[str, str]:
        row = self._conn.execute(
            """SELECT snapshot.source_response_id, snapshot.payload_json, snapshot.content_hash,
                      contract.provider_contract_id, contract.approval_evidence_id, contract.content_hash
               FROM am_provider_accounting_snapshot snapshot
               JOIN am_provider_accounting_contract contract
                 ON contract.provider_contract_id=snapshot.provider_contract_id
               WHERE snapshot.accounting_snapshot_id=? AND snapshot.runtime_run_id=?""",
            (accounting_snapshot_id, runtime_run_id),
        ).fetchone()
        if row is None:
            raise DataQualityError("CANONICAL_D2_ACCOUNTING_PROVENANCE_MISSING")
        try:
            payload = json.loads(str(row[1]))
            source = SQLiteRawResponseStore(self._conn).verified(str(row[0]))
            approval = SQLiteRawResponseStore(self._conn).verified(str(row[4]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvariantViolation("CANONICAL_D2_ACCOUNTING_PROVENANCE_UNVERIFIED") from exc
        if (not isinstance(payload, Mapping) or digest(canonical(payload)) != str(row[2]) or
                payload.get("runtime_run_id") != runtime_run_id or
                payload.get("source_response_id") != str(row[0]) or
                payload.get("source_response_hash") != source.response_hash or
                payload.get("provider_contract_id") != str(row[3]) or
                payload.get("provider_contract_hash") != str(row[5]) or
                source.received_at.astimezone(timezone.utc) > cutoff or
                approval.received_at.astimezone(timezone.utc) > cutoff):
            raise InvariantViolation("CANONICAL_D2_ACCOUNTING_PROVENANCE_UNVERIFIED")
        require_external_evidence_attestation(
            conn=self._conn, raw_response_id=str(row[0]), response=source, cutoff=cutoff,
            registry=registry)
        require_external_evidence_attestation(
            conn=self._conn, raw_response_id=str(row[4]), response=approval, cutoff=cutoff,
            registry=registry)
        return {"provider_contract_id": str(row[3]), "provider_contract_hash": str(row[5]),
                "approval_response_id": str(row[4]), "approval_response_hash": approval.response_hash,
                "source_response_id": str(row[0]), "source_response_hash": source.response_hash}

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
