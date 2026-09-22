"""Replayable D1 source bundle for one persisted canonical runtime run.

This boundary only assembles existing Feature/State/calculation/model-registry
records.  It never evaluates Gate D1 or turns a bundle into a PASS result.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.governance import ModelScope, RuntimeModelRegistryEvidenceRepository
from asset_management.time.clock import Clock


_HASH = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"git:[0-9a-f]{40}")


def _hash(value: object, reason: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise InvariantViolation(reason)
    return value


def _stored_utc(value: object, reason: str) -> datetime:
    try:
        result = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise InvariantViolation(reason) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise InvariantViolation(reason)
    return result.astimezone(timezone.utc)


def _payload(value: object, content_hash: object, reason: str) -> object:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise InvariantViolation(reason) from exc
    if digest(canonical(parsed)) != _hash(content_hash, reason):
        raise InvariantViolation(reason)
    return parsed


@dataclass(frozen=True, slots=True)
class CanonicalD1RuntimeEvidence:
    """Content-addressed source binding, never an acceptance decision."""

    runtime_run_id: str
    code_revision: str
    model_registry_snapshot_id: str
    model_registry_binding_hash: str
    catalog_object_id: str
    content_hash: str

    def __post_init__(self) -> None:
        if (not isinstance(self.runtime_run_id, str) or not self.runtime_run_id.strip() or
                not isinstance(self.code_revision, str) or _REVISION.fullmatch(self.code_revision) is None or
                not isinstance(self.model_registry_snapshot_id, str) or
                not self.model_registry_snapshot_id.strip()):
            raise InvariantViolation("CANONICAL_D1_EVIDENCE_INVALID")
        for value in (self.model_registry_binding_hash, self.catalog_object_id, self.content_hash):
            _hash(value, "CANONICAL_D1_EVIDENCE_INVALID")


class CanonicalD1RuntimeEvidenceRepository:
    """Bind D1 sources from one existing run; absence, ambiguity, and drift fail closed."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        if not isinstance(conn, sqlite3.Connection) or not hasattr(clock, "now_utc"):
            raise InvariantViolation("CANONICAL_D1_EVIDENCE_REPOSITORY_INVALID")
        self._conn = conn
        self._clock = clock
        self._conn.execute("PRAGMA foreign_keys=ON")

    @property
    def connection(self) -> sqlite3.Connection:
        """The persisted source connection for verification-only companion boundaries."""
        return self._conn

    def record(self, *, runtime_run_id: str, store: ImmutableDatasetStore) -> CanonicalD1RuntimeEvidence:
        result, body = self._assemble(runtime_run_id=runtime_run_id, store=store, publish_catalog=True)
        recorded_at = self._recorded_at()
        serialized = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        existing = self._conn.execute(
            """SELECT model_registry_snapshot_id, model_registry_binding_hash, catalog_object_id,
                      payload_json, content_hash FROM am_canonical_d1_runtime_evidence
               WHERE runtime_run_id=?""", (runtime_run_id,)).fetchone()
        if existing is not None:
            expected = (result.model_registry_snapshot_id, result.model_registry_binding_hash,
                        result.catalog_object_id, serialized, result.content_hash)
            if tuple(existing) != expected:
                raise InvariantViolation("CANONICAL_D1_EVIDENCE_CONFLICT")
            return result
        with self._conn:
            self._conn.execute(
                """INSERT INTO am_canonical_d1_runtime_evidence
                   (runtime_run_id, model_registry_snapshot_id, model_registry_binding_hash,
                    catalog_object_id, payload_json, content_hash, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (runtime_run_id, result.model_registry_snapshot_id,
                 result.model_registry_binding_hash, result.catalog_object_id, serialized,
                 result.content_hash, recorded_at),
            )
        return result

    def replay(self, *, runtime_run_id: str, store: ImmutableDatasetStore) -> CanonicalD1RuntimeEvidence:
        if not isinstance(runtime_run_id, str) or not runtime_run_id.strip():
            raise InvariantViolation("CANONICAL_D1_EVIDENCE_INVALID")
        row = self._conn.execute(
            "SELECT payload_json, content_hash, catalog_object_id FROM am_canonical_d1_runtime_evidence "
            "WHERE runtime_run_id=?", (runtime_run_id,)).fetchone()
        if row is None:
            raise DataQualityError("CANONICAL_D1_EVIDENCE_MISSING")
        try:
            stored = json.loads(str(row[0]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvariantViolation("CANONICAL_D1_EVIDENCE_INVALID") from exc
        if digest(canonical(stored)) != _hash(row[1], "CANONICAL_D1_EVIDENCE_INVALID"):
            raise InvariantViolation("CANONICAL_D1_EVIDENCE_INVALID")
        result, body = self._assemble(runtime_run_id=runtime_run_id, store=store, publish_catalog=False)
        if body != stored or result.content_hash != row[1] or result.catalog_object_id != row[2]:
            raise InvariantViolation("CANONICAL_D1_EVIDENCE_REPLAY_MISMATCH")
        return result

    def runtime_information_cutoff(self, *, runtime_run_id: str,
                                   code_revision: str) -> datetime:
        """Return the persisted cutoff for a replayed runtime, never caller time.

        This narrow accessor is intentionally read-only.  Gate D1 uses it only
        to select a runtime-bound external authority; callers cannot nominate a
        later cutoff to make an attestation appear valid.
        """
        if (not isinstance(runtime_run_id, str) or not runtime_run_id.strip() or
                not isinstance(code_revision, str) or _REVISION.fullmatch(code_revision) is None):
            raise InvariantViolation("CANONICAL_D1_RUNTIME_INVALID")
        row = self._conn.execute(
            "SELECT as_of_utc, information_cutoff_utc, code_revision FROM am_runtime_run "
            "WHERE runtime_run_id=?", (runtime_run_id,)).fetchone()
        if row is None:
            raise DataQualityError("CANONICAL_D1_RUNTIME_MISSING")
        as_of = _stored_utc(row[0], "CANONICAL_D1_RUNTIME_INVALID")
        cutoff = _stored_utc(row[1], "CANONICAL_D1_RUNTIME_INVALID")
        if cutoff > as_of or row[2] != code_revision:
            raise InvariantViolation("CANONICAL_D1_RUNTIME_INVALID")
        return cutoff

    def _assemble(self, *, runtime_run_id: str, store: ImmutableDatasetStore,
                  publish_catalog: bool) -> tuple[CanonicalD1RuntimeEvidence, dict[str, object]]:
        if not isinstance(runtime_run_id, str) or not runtime_run_id.strip() or not isinstance(store, ImmutableDatasetStore):
            raise InvariantViolation("CANONICAL_D1_EVIDENCE_INVALID")
        runtime = self._conn.execute(
            "SELECT as_of_utc, information_cutoff_utc, code_revision, created_at_utc "
            "FROM am_runtime_run WHERE runtime_run_id=?", (runtime_run_id,)).fetchone()
        if runtime is None:
            raise DataQualityError("CANONICAL_D1_RUNTIME_MISSING")
        as_of = _stored_utc(runtime[0], "CANONICAL_D1_RUNTIME_INVALID")
        cutoff = _stored_utc(runtime[1], "CANONICAL_D1_RUNTIME_INVALID")
        created = _stored_utc(runtime[3], "CANONICAL_D1_RUNTIME_INVALID")
        if cutoff > as_of or created > as_of or not isinstance(runtime[2], str) or _REVISION.fullmatch(runtime[2]) is None:
            raise InvariantViolation("CANONICAL_D1_RUNTIME_INVALID")

        snapshot_id, binding_hash, snapshot_hash, authorized = self._model_registry(runtime_run_id)
        features = self._features(runtime_run_id, store, cutoff=cutoff,
                                  authorized_model_keys=authorized[ModelScope.FEATURE_CALCULATION])
        states = self._states(runtime_run_id, authorized_model_keys=authorized[ModelScope.STATE_INFERENCE])
        calculations = self._calculations(
            runtime_run_id,
            pricing_model_keys=authorized[ModelScope.PRICING_BASELINE_RETURN],
            expectation_model_keys=authorized[ModelScope.EXPECTED_RETURN],
        )
        if {str(item["feature_run_id"]) for item in features} != {
                str(item["feature_run_id"]) for item in states}:
            raise DataQualityError("CANONICAL_D1_STATE_COVERAGE_MISSING")
        if {str(item["state_run_id"]) for item in states} != {
                str(item["state_run_id"]) for item in calculations}:
            raise DataQualityError("CANONICAL_D1_CALCULATION_COVERAGE_MISSING")
        body: dict[str, object] = {
            "schema_version": "canonical-d1-runtime-evidence@1",
            "runtime": {"runtime_run_id": runtime_run_id, "as_of": as_of.isoformat(),
                        "information_cutoff": cutoff.isoformat(), "code_revision": runtime[2],
                        "created_at": created.isoformat()},
            "features": features,
            "states": states,
            "calculation_lineage": calculations,
            "model_registry": {"snapshot_id": snapshot_id, "snapshot_content_hash": snapshot_hash,
                               "runtime_binding_hash": binding_hash},
        }
        content_hash = digest(canonical(body))
        catalog_id = content_hash
        if publish_catalog:
            if store.catalog("canonical-d1-runtime-evidence", body) != catalog_id:
                raise InvariantViolation("CANONICAL_D1_EVIDENCE_CATALOG_INVALID")
        else:
            try:
                catalog = store.layout.resolve(
                    "catalog", f"canonical-d1-runtime-evidence/{catalog_id}.json").read_bytes()
            except OSError as exc:
                raise DataQualityError("CANONICAL_D1_EVIDENCE_CATALOG_MISSING") from exc
            if catalog != canonical(body):
                raise InvariantViolation("CANONICAL_D1_EVIDENCE_CATALOG_INVALID")
        return CanonicalD1RuntimeEvidence(runtime_run_id, str(runtime[2]), snapshot_id, binding_hash,
                                          catalog_id, content_hash), body

    def _features(self, runtime_run_id: str, store: ImmutableDatasetStore, *,
                  cutoff: datetime, authorized_model_keys: tuple[str, ...]) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """SELECT feature.feature_run_id, feature.dataset_manifest_id, feature.feature_version,
                      feature.payload_json, feature.content_hash
               FROM am_feature_run feature WHERE feature.runtime_run_id=?
               ORDER BY feature.feature_run_id""", (runtime_run_id,)).fetchall()
        if not rows:
            raise DataQualityError("CANONICAL_D1_FEATURE_EVIDENCE_MISSING")
        result: list[dict[str, object]] = []
        for feature_id, manifest_id, version, payload_raw, content_hash in rows:
            _payload(payload_raw, content_hash, "CANONICAL_D1_FEATURE_PAYLOAD_INVALID")
            if str(version) not in authorized_model_keys:
                raise DataQualityError("CANONICAL_D1_FEATURE_MODEL_UNAUTHORIZED")
            result.append({"feature_run_id": str(feature_id), "feature_version": str(version),
                           "content_hash": _hash(content_hash, "CANONICAL_D1_FEATURE_PAYLOAD_INVALID"),
                           "manifest_lineage": self._manifest_lineage(
                               str(manifest_id), runtime_run_id, store, cutoff=cutoff)})
        return result

    def _states(self, runtime_run_id: str, *, authorized_model_keys: tuple[str, ...]) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """SELECT state.state_run_id, state.feature_run_id, state.state_version,
                      state.payload_json, state.content_hash
               FROM am_state_run state JOIN am_feature_run feature USING(feature_run_id)
               WHERE feature.runtime_run_id=? ORDER BY state.state_run_id""", (runtime_run_id,)).fetchall()
        if not rows:
            raise DataQualityError("CANONICAL_D1_STATE_EVIDENCE_MISSING")
        result = []
        for state_id, feature_id, version, payload_raw, content_hash in rows:
            _payload(payload_raw, content_hash, "CANONICAL_D1_STATE_PAYLOAD_INVALID")
            if str(version) not in authorized_model_keys:
                raise DataQualityError("CANONICAL_D1_STATE_MODEL_UNAUTHORIZED")
            result.append({"state_run_id": str(state_id), "feature_run_id": str(feature_id),
                           "state_version": str(version),
                           "content_hash": _hash(content_hash, "CANONICAL_D1_STATE_PAYLOAD_INVALID")})
        return result

    def _calculations(self, runtime_run_id: str, *, pricing_model_keys: tuple[str, ...],
                      expectation_model_keys: tuple[str, ...]) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """SELECT pricing.pricing_run_id, pricing.state_run_id, pricing.pricing_version,
                      pricing.payload_json, pricing.content_hash, expectation.expectation_run_id,
                      expectation.expectation_version, expectation.payload_json, expectation.content_hash
               FROM am_pricing_run pricing
               JOIN am_state_run state ON state.state_run_id=pricing.state_run_id
               JOIN am_feature_run feature ON feature.feature_run_id=state.feature_run_id
               JOIN am_expectation_run expectation ON expectation.pricing_run_id=pricing.pricing_run_id
               WHERE feature.runtime_run_id=? ORDER BY expectation.expectation_run_id""", (runtime_run_id,)).fetchall()
        if not rows:
            raise DataQualityError("CANONICAL_D1_CALCULATION_LINEAGE_MISSING")
        result = []
        for pricing_id, state_id, pricing_version, pricing_payload, pricing_hash, expectation_id, expectation_version, expectation_payload, expectation_hash in rows:
            _payload(pricing_payload, pricing_hash, "CANONICAL_D1_PRICING_PAYLOAD_INVALID")
            _payload(expectation_payload, expectation_hash, "CANONICAL_D1_EXPECTATION_PAYLOAD_INVALID")
            if str(pricing_version) not in pricing_model_keys:
                raise DataQualityError("CANONICAL_D1_PRICING_MODEL_UNAUTHORIZED")
            if str(expectation_version) not in expectation_model_keys:
                raise DataQualityError("CANONICAL_D1_EXPECTATION_MODEL_UNAUTHORIZED")
            result.append({"pricing_run_id": str(pricing_id), "state_run_id": str(state_id),
                           "pricing_version": str(pricing_version),
                           "pricing_content_hash": _hash(pricing_hash, "CANONICAL_D1_PRICING_PAYLOAD_INVALID"),
                           "expectation_run_id": str(expectation_id),
                           "expectation_version": str(expectation_version),
                           "expectation_content_hash": _hash(expectation_hash, "CANONICAL_D1_EXPECTATION_PAYLOAD_INVALID")})
        return result

    def _manifest_lineage(self, manifest_id: str, runtime_run_id: str,
                          store: ImmutableDatasetStore, *, cutoff: datetime) -> list[dict[str, object]]:
        seen: set[str] = set()
        result: list[dict[str, object]] = []

        def visit(identifier: str) -> None:
            if identifier in seen:
                return
            seen.add(identifier)
            try:
                manifest, _ = store.read(identifier)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise DataQualityError("CANONICAL_D1_MANIFEST_UNVERIFIED") from exc
            row = self._conn.execute(
                """SELECT manifest.content_hash, manifest.layer, manifest.dataset_name,
                          manifest.observed_at_utc, manifest.received_at_utc
                   FROM am_dataset_manifest manifest JOIN am_ingestion_run ingestion USING(ingestion_run_id)
                   WHERE manifest.dataset_manifest_id=? AND ingestion.runtime_run_id=?""",
                (identifier, runtime_run_id)).fetchone()
            if (row is None or row[0] != manifest.content_sha256 or row[1] != manifest.layer or
                    row[2] != manifest.dataset):
                raise DataQualityError("CANONICAL_D1_MANIFEST_UNVERIFIED")
            observed = _stored_utc(row[3], "CANONICAL_D1_MANIFEST_UNVERIFIED")
            received = _stored_utc(row[4], "CANONICAL_D1_MANIFEST_UNVERIFIED")
            available = _stored_utc(manifest.available_at, "CANONICAL_D1_MANIFEST_UNVERIFIED")
            if observed > cutoff or received > cutoff or available > cutoff:
                raise DataQualityError("CANONICAL_D1_MANIFEST_NOT_POINT_IN_TIME")
            for parent in manifest.parent_manifest_ids:
                visit(parent)
            result.append({"dataset_manifest_id": identifier, "layer": manifest.layer,
                           "content_hash": manifest.content_sha256,
                           "available_at": available.isoformat(),
                           "parent_manifest_ids": list(manifest.parent_manifest_ids)})

        visit(manifest_id)
        if not any(item["layer"] == "bronze" for item in result):
            raise DataQualityError("CANONICAL_D1_BRONZE_LINEAGE_MISSING")
        return sorted(result, key=lambda item: str(item["dataset_manifest_id"]))

    def _model_registry(self, runtime_run_id: str) -> tuple[str, str, str, dict[ModelScope, tuple[str, ...]]]:
        registry = RuntimeModelRegistryEvidenceRepository(self._conn, self._clock)
        # Each call replays the selected binding.  No caller-selected model may
        # substitute for the runtime's persisted, review-backed snapshot.
        authorized = {scope: registry.authorized_model_keys(runtime_run_id, scope=scope)
                      for scope in ModelScope}
        row = self._conn.execute(
            """SELECT binding.model_registry_snapshot_id, binding.content_hash, snapshot.content_hash
               FROM am_runtime_model_registry binding
               JOIN am_model_registry_snapshot snapshot
                 ON snapshot.model_registry_snapshot_id=binding.model_registry_snapshot_id
               WHERE binding.runtime_run_id=?""", (runtime_run_id,)).fetchone()
        if row is None:
            raise DataQualityError("CANONICAL_D1_MODEL_REGISTRY_MISSING")
        return (str(row[0]), _hash(row[1], "CANONICAL_D1_MODEL_REGISTRY_INVALID"),
                _hash(row[2], "CANONICAL_D1_MODEL_REGISTRY_INVALID"), authorized)

    def _recorded_at(self) -> str:
        value = self._clock.now_utc()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise InvariantViolation("CANONICAL_D1_EVIDENCE_TIME_INVALID")
        return value.astimezone(timezone.utc).isoformat()
