"""Persisted model-registry authority selected by one canonical runtime run."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from collections.abc import Mapping

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.time.clock import Clock

from .model_registry import ModelAuthorization, ModelRegistry, ModelScope, ModelStatus


def _utc(value: object, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolation(reason)
    return value.astimezone(timezone.utc)


def _stored_utc(value: object, reason: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise InvariantViolation(reason) from exc
    return _utc(parsed, reason)


def _hash(value: object, reason: str) -> str:
    if (not isinstance(value, str) or len(value) != 64 or
            any(character not in "0123456789abcdef" for character in value)):
        raise InvariantViolation(reason)
    return value


@dataclass(frozen=True, slots=True)
class RuntimeModelAuthorization:
    """A model authorization that is inseparable from a persisted runtime run."""

    runtime_run_id: str
    model_registry_snapshot_id: str
    binding_hash: str
    authorization: ModelAuthorization

    def __post_init__(self) -> None:
        if (not isinstance(self.runtime_run_id, str) or not self.runtime_run_id.strip() or
                not isinstance(self.model_registry_snapshot_id, str) or
                not self.model_registry_snapshot_id.strip()):
            raise InvariantViolation("MODEL_RUNTIME_AUTHORIZATION_INVALID")
        _hash(self.binding_hash, "MODEL_RUNTIME_AUTHORIZATION_INVALID")
        if not isinstance(self.authorization, ModelAuthorization):
            raise InvariantViolation("MODEL_RUNTIME_AUTHORIZATION_INVALID")


class RuntimeModelRegistryEvidenceRepository:
    """Append-only registry snapshots and their point-in-time runtime selection."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        if not isinstance(conn, sqlite3.Connection) or not hasattr(clock, "now_utc"):
            raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_REPOSITORY_INVALID")
        self._conn = conn
        self._clock = clock
        self._conn.execute("PRAGMA foreign_keys=ON")

    def record_review_evidence(self, evidence_id: str, *, model_key: str,
                               from_status: ModelStatus, to_status: ModelStatus,
                               owner: str, evidence: Mapping[str, object]) -> str:
        """Append a review artifact before a lifecycle transition may use it."""
        if (not isinstance(evidence_id, str) or not evidence_id.strip() or
                not isinstance(model_key, str) or not model_key.strip() or
                not isinstance(from_status, ModelStatus) or not isinstance(to_status, ModelStatus) or
                not isinstance(owner, str) or not owner.strip() or not isinstance(evidence, Mapping)):
            raise InvariantViolation("MODEL_REVIEW_EVIDENCE_INVALID")
        recorded = _utc(self._clock.now_utc(), "MODEL_REVIEW_EVIDENCE_TIME_INVALID")
        body = {"evidence_id": evidence_id, "model_key": model_key,
                "from_status": from_status.value, "to_status": to_status.value,
                "owner": owner, "evidence": dict(evidence),
                "recorded_at": recorded.isoformat()}
        content_hash = digest(canonical(body))
        existing = self._conn.execute(
            "SELECT payload_json, content_hash FROM am_model_governance_review_evidence WHERE review_evidence_id=?",
            (content_hash,),
        ).fetchone()
        if existing is not None:
            try:
                stored = json.loads(str(existing[0]))
            except (TypeError, json.JSONDecodeError) as exc:
                raise InvariantViolation("MODEL_REVIEW_EVIDENCE_CONFLICT") from exc
            if stored != body or existing[1] != content_hash:
                raise InvariantViolation("MODEL_REVIEW_EVIDENCE_CONFLICT")
            return content_hash
        with self._conn:
            self._conn.execute(
                """INSERT INTO am_model_governance_review_evidence
                   (review_evidence_id, evidence_id, payload_json, content_hash, recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?)""",
                (content_hash, evidence_id,
                 json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 content_hash, recorded.isoformat()),
            )
        return content_hash

    def publish_snapshot(self, registry: ModelRegistry) -> str:
        """Persist exact, review-backed governance at repository ingestion time."""
        if not isinstance(registry, ModelRegistry):
            raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_INVALID")
        at = _utc(self._clock.now_utc(), "MODEL_REGISTRY_SNAPSHOT_TIME_INVALID")
        payload = registry.payload()
        # Reconstruct before storage so a mutated/internal registry can never be
        # treated as an immutable snapshot merely because it has a hash label.
        ModelRegistry.from_payload(payload)
        snapshot_id = registry.registry_hash
        existing = self._conn.execute(
            """SELECT registry_hash, payload_json, review_evidence_json, content_hash, published_at_utc
               FROM am_model_registry_snapshot WHERE model_registry_snapshot_id=?""",
            (snapshot_id,),
        ).fetchone()
        if existing is not None:
            try:
                stored_payload = json.loads(str(existing[1]))
            except (TypeError, json.JSONDecodeError) as exc:
                raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_CONFLICT") from exc
            if existing[0] != snapshot_id or stored_payload != payload:
                raise InvariantViolation("MODEL_REGISTRY_SNAPSHOT_CONFLICT")
            return snapshot_id
        reviews = self._reviews_for_registry(registry, published_at=at)
        content_hash = digest(canonical({"registry": payload, "review_evidence": reviews,
                                         "published_at": at.isoformat()}))
        with self._conn:
            self._conn.execute(
                """INSERT INTO am_model_registry_snapshot
                   (model_registry_snapshot_id, registry_hash, payload_json, review_evidence_json,
                    content_hash, published_at_utc) VALUES (?, ?, ?, ?, ?, ?)""",
                (snapshot_id, snapshot_id, json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                                       separators=(",", ":")),
                 json.dumps(reviews, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 content_hash, at.isoformat()),
            )
        return snapshot_id

    def bind_runtime_run(self, runtime_run_id: str, model_registry_snapshot_id: str) -> str:
        """Select an already-persisted snapshot for exactly one runtime run."""
        if (not isinstance(runtime_run_id, str) or not runtime_run_id.strip() or
                not isinstance(model_registry_snapshot_id, str) or not model_registry_snapshot_id.strip()):
            raise InvariantViolation("MODEL_RUNTIME_BINDING_INVALID")
        bound = _utc(self._clock.now_utc(), "MODEL_RUNTIME_BINDING_TIME_INVALID")
        row = self._conn.execute(
            """SELECT runtime.as_of_utc, runtime.information_cutoff_utc, runtime.code_revision,
                      runtime.created_at_utc, snapshot.registry_hash, snapshot.payload_json,
                      snapshot.review_evidence_json, snapshot.content_hash, snapshot.published_at_utc
               FROM am_runtime_run runtime
               JOIN am_model_registry_snapshot snapshot
                 ON snapshot.model_registry_snapshot_id=?
               WHERE runtime.runtime_run_id=?""",
            (model_registry_snapshot_id, runtime_run_id),
        ).fetchone()
        if row is None:
            raise InvariantViolation("MODEL_RUNTIME_SNAPSHOT_UNAVAILABLE")
        as_of = _stored_utc(row[0], "MODEL_RUNTIME_EVIDENCE_INVALID")
        cutoff = _stored_utc(row[1], "MODEL_RUNTIME_EVIDENCE_INVALID")
        created = _stored_utc(row[3], "MODEL_RUNTIME_EVIDENCE_INVALID")
        published = _stored_utc(row[8], "MODEL_RUNTIME_EVIDENCE_INVALID")
        if (cutoff > as_of or published > cutoff or bound < created or bound > as_of or
                self._conn.execute("SELECT 1 FROM am_pipeline_stage_evidence WHERE runtime_run_id=? LIMIT 1",
                                   (runtime_run_id,)).fetchone() is not None):
            raise InvariantViolation("MODEL_RUNTIME_SNAPSHOT_NOT_POINT_IN_TIME")
        registry = self._registry_from_row(row[4], row[5], row[6], row[7], published)
        body = {"runtime_run_id": runtime_run_id,
                "model_registry_snapshot_id": model_registry_snapshot_id,
                "registry_hash": registry.registry_hash, "snapshot_content_hash": row[7],
                "runtime": {"as_of": as_of.isoformat(), "information_cutoff": cutoff.isoformat(),
                            "code_revision": str(row[2]), "created_at": created.isoformat()},
                "bound_at": bound.isoformat()}
        binding_hash = digest(canonical(body))
        existing = self._conn.execute(
            """SELECT model_registry_snapshot_id, bound_at_utc, content_hash
               FROM am_runtime_model_registry WHERE runtime_run_id=?""", (runtime_run_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != (model_registry_snapshot_id, bound.isoformat(), binding_hash):
                raise InvariantViolation("MODEL_RUNTIME_BINDING_CONFLICT")
            return binding_hash
        with self._conn:
            self._conn.execute(
                """INSERT INTO am_runtime_model_registry
                   (runtime_run_id, model_registry_snapshot_id, bound_at_utc, content_hash)
                   VALUES (?, ?, ?, ?)""",
                (runtime_run_id, model_registry_snapshot_id, bound.isoformat(), binding_hash),
            )
        return binding_hash

    def authorize(self, runtime_run_id: str, *, model_key: str,
                  scope: ModelScope) -> RuntimeModelAuthorization:
        """Authorize only through the runtime's immutable selected snapshot."""
        registry, snapshot_id, binding_hash, cutoff, _ = self._selected(runtime_run_id)
        authorization = registry.authorize(model_key, scope, at=cutoff)
        return RuntimeModelAuthorization(runtime_run_id, snapshot_id, binding_hash, authorization)

    def require_authorization(self, runtime_authorization: RuntimeModelAuthorization, *,
                              model_key: str, scope: ModelScope, at: datetime) -> ModelRegistry:
        """Re-read the persisted binding before allowing a model calculation."""
        if not isinstance(runtime_authorization, RuntimeModelAuthorization):
            raise InvariantViolation("MODEL_RUNTIME_AUTHORIZATION_MISSING")
        requested_at = _utc(at, "MODEL_RUNTIME_AUTHORIZATION_TIME_INVALID")
        registry, snapshot_id, binding_hash, cutoff, as_of = self._selected(
            runtime_authorization.runtime_run_id)
        if (runtime_authorization.model_registry_snapshot_id != snapshot_id or
                runtime_authorization.binding_hash != binding_hash or requested_at < cutoff or
                requested_at > as_of):
            raise InvariantViolation("MODEL_RUNTIME_AUTHORIZATION_INVALID")
        registry.require_authorization(runtime_authorization.authorization,
                                       model_key=model_key, scope=scope, at=requested_at)
        return registry

    def _selected(self, runtime_run_id: str) -> tuple[ModelRegistry, str, str, datetime, datetime]:
        if not isinstance(runtime_run_id, str) or not runtime_run_id.strip():
            raise InvariantViolation("MODEL_RUNTIME_BINDING_INVALID")
        row = self._conn.execute(
            """SELECT binding.model_registry_snapshot_id, binding.bound_at_utc, binding.content_hash,
                      runtime.as_of_utc, runtime.information_cutoff_utc, runtime.code_revision,
                      runtime.created_at_utc, snapshot.registry_hash, snapshot.payload_json,
                      snapshot.review_evidence_json, snapshot.content_hash, snapshot.published_at_utc
               FROM am_runtime_model_registry binding
               JOIN am_runtime_run runtime ON runtime.runtime_run_id=binding.runtime_run_id
               JOIN am_model_registry_snapshot snapshot
                 ON snapshot.model_registry_snapshot_id=binding.model_registry_snapshot_id
               WHERE binding.runtime_run_id=?""", (runtime_run_id,),
        ).fetchone()
        if row is None:
            raise InvariantViolation("MODEL_RUNTIME_BINDING_MISSING")
        snapshot_id, bound_at, binding_hash, as_of_raw, cutoff_raw, code_revision, created_raw, registry_hash, payload_raw, reviews_raw, content_hash, published_at = row
        as_of = _stored_utc(as_of_raw, "MODEL_RUNTIME_EVIDENCE_INVALID")
        cutoff = _stored_utc(cutoff_raw, "MODEL_RUNTIME_EVIDENCE_INVALID")
        created = _stored_utc(created_raw, "MODEL_RUNTIME_EVIDENCE_INVALID")
        published = _stored_utc(published_at, "MODEL_RUNTIME_EVIDENCE_INVALID")
        bound = _stored_utc(bound_at, "MODEL_RUNTIME_EVIDENCE_INVALID")
        if cutoff > as_of or published > cutoff or bound < created or bound > as_of:
            raise InvariantViolation("MODEL_RUNTIME_SNAPSHOT_NOT_POINT_IN_TIME")
        registry = self._registry_from_row(registry_hash, payload_raw, reviews_raw, content_hash, published)
        expected_binding_hash = digest(canonical({
            "runtime_run_id": runtime_run_id, "model_registry_snapshot_id": snapshot_id,
            "registry_hash": registry.registry_hash, "snapshot_content_hash": content_hash,
            "runtime": {"as_of": as_of.isoformat(), "information_cutoff": cutoff.isoformat(),
                        "code_revision": str(code_revision), "created_at": created.isoformat()},
            "bound_at": bound.isoformat(),
        }))
        if binding_hash != expected_binding_hash:
            raise InvariantViolation("MODEL_RUNTIME_BINDING_INVALID")
        return registry, str(snapshot_id), str(binding_hash), cutoff, as_of

    def _reviews_for_registry(self, registry: ModelRegistry, *, published_at: datetime) -> list[dict[str, object]]:
        reviews: list[dict[str, object]] = []
        for transition in registry._transitions:
            owner = registry.models[transition.model_key].owner
            for evidence_id in transition.evidence_ids:
                rows = self._conn.execute(
                    "SELECT payload_json, content_hash, recorded_at_utc FROM am_model_governance_review_evidence WHERE evidence_id=?",
                    (evidence_id,),
                ).fetchall()
                matched = None
                for row in rows:
                    try:
                        payload = json.loads(str(row[0]))
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise InvariantViolation("MODEL_REVIEW_EVIDENCE_INVALID") from exc
                    recorded = _stored_utc(row[2], "MODEL_REVIEW_EVIDENCE_INVALID")
                    payload_recorded = _stored_utc(payload.get("recorded_at"), "MODEL_REVIEW_EVIDENCE_INVALID")
                    if (digest(canonical(payload)) == row[1] and payload_recorded == recorded and
                            payload.get("evidence_id") == evidence_id and
                            payload.get("model_key") == transition.model_key and
                            payload.get("from_status") == transition.from_status.value and
                            payload.get("to_status") == transition.to_status.value and
                            payload.get("owner") == owner and recorded <= transition.effective_at and
                            recorded <= published_at):
                        matched = row
                        break
                if matched is None:
                    raise InvariantViolation("MODEL_REVIEW_EVIDENCE_INVALID")
                reviews.append({"evidence_id": evidence_id, "content_hash": matched[1],
                                "recorded_at": _stored_utc(matched[2], "MODEL_REVIEW_EVIDENCE_INVALID").isoformat()})
        return sorted(reviews, key=lambda item: (str(item["evidence_id"]), str(item["content_hash"])))

    def _registry_from_row(self, registry_hash: object, payload_raw: object, reviews_raw: object,
                           content_hash: object, published_at: datetime) -> ModelRegistry:
        _hash(registry_hash, "MODEL_RUNTIME_EVIDENCE_INVALID")
        _hash(content_hash, "MODEL_RUNTIME_EVIDENCE_INVALID")
        try:
            payload = json.loads(str(payload_raw))
            reviews = json.loads(str(reviews_raw))
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_INVALID") from exc
        if (not isinstance(reviews, list) or digest(canonical({"registry": payload,
                "review_evidence": reviews, "published_at": published_at.isoformat()})) != content_hash):
            raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_INVALID")
        for review in reviews:
            if (not isinstance(review, dict) or set(review) != {"evidence_id", "content_hash", "recorded_at"}):
                raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_INVALID")
            evidence_id = review["evidence_id"]
            review_hash = review["content_hash"]
            recorded = _stored_utc(review["recorded_at"], "MODEL_RUNTIME_EVIDENCE_INVALID")
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_INVALID")
            _hash(review_hash, "MODEL_RUNTIME_EVIDENCE_INVALID")
            row = self._conn.execute(
                """SELECT payload_json, recorded_at_utc FROM am_model_governance_review_evidence
                   WHERE evidence_id=? AND content_hash=?""", (evidence_id, review_hash),
            ).fetchone()
            if row is None:
                raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_INVALID")
            try:
                review_payload = json.loads(str(row[0]))
            except (TypeError, json.JSONDecodeError) as exc:
                raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_INVALID") from exc
            stored_recorded = _stored_utc(row[1], "MODEL_RUNTIME_EVIDENCE_INVALID")
            payload_recorded = _stored_utc(review_payload.get("recorded_at"), "MODEL_RUNTIME_EVIDENCE_INVALID")
            if (digest(canonical(review_payload)) != review_hash or stored_recorded != recorded or
                    payload_recorded != recorded):
                raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_INVALID")
        registry = ModelRegistry.from_payload(payload)
        if registry.registry_hash != registry_hash:
            raise InvariantViolation("MODEL_RUNTIME_EVIDENCE_INVALID")
        return registry
