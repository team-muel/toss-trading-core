"""Verification-only boundary for independently signed evidence receipts."""
from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from asset_management.data.immutable import canonical, digest
from asset_management.data.raw_store import RawApiResponse
from asset_management.domain.errors import DataQualityError, InvariantViolation


_SCHEMA = "canonical-evidence-attestor-registry@1"
_REGISTRY_AUTHORIZATION_SCHEMA = "canonical-evidence-attestor-registry-authorization@1"
_PAYLOAD_SCHEMA = "external-evidence-attestation@1"

# This is deliberately not deployment configuration.  Adding or rotating a
# registry authority is a reviewed code change and is bound by code_revision.
# Until an operational governance authority is approved here, canonical runs
# fail closed instead of trusting a caller-provided registry or key.
_TRUSTED_REGISTRY_AUTHORITIES: Mapping[str, bytes] = {}


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _registry(payload: object) -> dict[str, tuple[bytes, datetime, datetime | None]]:
    document = payload
    if not isinstance(document, Mapping) or document.get("schema_version") != _SCHEMA or not isinstance(document.get("attestors"), list):
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
    result: dict[str, tuple[bytes, datetime, datetime | None]] = {}
    for item in document["attestors"]:
        if not isinstance(item, Mapping) or set(item) != {"attestor_id", "algorithm", "public_key_base64", "effective_from_utc", "effective_to_utc"}:
            raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
        identifier = item.get("attestor_id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in result or item.get("algorithm") != "ed25519":
            raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
        try:
            key = b64decode(str(item["public_key_base64"]), validate=True)
            Ed25519PublicKey.from_public_bytes(key)
            start = _utc(item["effective_from_utc"])
            end = None if item["effective_to_utc"] is None else _utc(item["effective_to_utc"])
        except (TypeError, ValueError, Base64Error) as exc:
            raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID") from exc
        if end is not None and end <= start:
            raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
        result[identifier] = (key, start, end)
    return result


def registry_authorization_payload(*, authority_id: str, snapshot_id: str,
                                   registry_hash: str, published_at: datetime) -> dict[str, str]:
    if (not isinstance(authority_id, str) or not authority_id.strip() or
            not isinstance(snapshot_id, str) or not snapshot_id.strip() or
            not isinstance(registry_hash, str) or not registry_hash.strip() or
            published_at.tzinfo is None or published_at.utcoffset() is None):
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
    return {
        "schema_version": _REGISTRY_AUTHORIZATION_SCHEMA,
        "authority_id": authority_id,
        "evidence_attestor_registry_snapshot_id": snapshot_id,
        "registry_hash": registry_hash,
        "published_at": published_at.astimezone(timezone.utc).isoformat(),
    }


@dataclass(frozen=True, slots=True)
class RuntimeAttestorRegistry:
    snapshot_id: str
    content_hash: str
    bound_at: datetime
    attestors: dict[str, tuple[bytes, datetime, datetime | None]]


def require_runtime_attestor_registry(*, conn: sqlite3.Connection, runtime_run_id: str,
                                      cutoff: datetime) -> RuntimeAttestorRegistry:
    """Load the immutable trust root selected for this runtime before cutoff."""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
    row = conn.execute(
        """SELECT binding.evidence_attestor_registry_snapshot_id, binding.bound_at_utc,
                  binding.content_hash, snapshot.payload_json, snapshot.content_hash,
                  snapshot.published_at_utc, snapshot.authority_id,
                  snapshot.authorization_payload_json, snapshot.authority_signature_base64,
                  runtime.as_of_utc, runtime.information_cutoff_utc, runtime.code_revision,
                  runtime.created_at_utc
           FROM am_runtime_evidence_attestor_registry binding
           JOIN am_evidence_attestor_registry_snapshot snapshot
             ON snapshot.evidence_attestor_registry_snapshot_id=binding.evidence_attestor_registry_snapshot_id
           JOIN am_runtime_run runtime ON runtime.runtime_run_id=binding.runtime_run_id
           WHERE binding.runtime_run_id=?""", (runtime_run_id,)
    ).fetchone()
    if row is None:
        raise DataQualityError("CANONICAL_D2_ATTESTOR_REGISTRY_MISSING")
    try:
        (snapshot_id, bound_at, binding_hash, payload_raw, snapshot_hash, published_at,
         authority_id, authorization_raw, authority_signature, as_of, stored_cutoff,
         code_revision, created_at) = row
        payload = json.loads(str(payload_raw))
        authorization = json.loads(str(authorization_raw))
        signature = b64decode(str(authority_signature), validate=True)
        bound = _utc(bound_at); published = _utc(published_at); as_of_time = _utc(as_of)
        cutoff_time = _utc(stored_cutoff); created = _utc(created_at)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID") from exc
    registry_hash = digest(canonical(payload))
    authorization_payload = registry_authorization_payload(
        authority_id=str(authority_id), snapshot_id=str(snapshot_id), registry_hash=registry_hash,
        published_at=published)
    authorization_hash = digest(canonical({"payload": authorization_payload,
                                           "signature_base64": str(authority_signature)}))
    if (cutoff_time != cutoff.astimezone(timezone.utc) or cutoff_time > as_of_time or
            published > cutoff_time or bound < created or bound > as_of_time or
            str(snapshot_id) != registry_hash or authorization != authorization_payload or
            str(snapshot_hash) != digest(canonical({"registry": payload, "published_at": published.isoformat(),
                                                     "registry_authorization_hash": authorization_hash})) or
            str(binding_hash) != digest(canonical({
                "runtime_run_id": runtime_run_id,
                "evidence_attestor_registry_snapshot_id": str(snapshot_id),
                "snapshot_content_hash": str(snapshot_hash),
                "runtime": {"as_of": as_of_time.isoformat(), "information_cutoff": cutoff_time.isoformat(),
                            "code_revision": str(code_revision), "created_at": created.isoformat()},
                "bound_at": bound.isoformat(),
            }))):
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
    authority_key = _TRUSTED_REGISTRY_AUTHORITIES.get(str(authority_id))
    if authority_key is None:
        raise DataQualityError("CANONICAL_D2_ATTESTOR_REGISTRY_UNTRUSTED")
    try:
        Ed25519PublicKey.from_public_bytes(authority_key).verify(signature, canonical(authorization_payload))
    except (ValueError, InvalidSignature) as exc:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID") from exc
    return RuntimeAttestorRegistry(str(snapshot_id), str(snapshot_hash), bound, _registry(payload))


def attestation_payload(*, raw_response_id: str, response: RawApiResponse,
                        attestor_id: str, issued_at: datetime,
                        registry: RuntimeAttestorRegistry) -> dict[str, object]:
    if issued_at.tzinfo is None or issued_at.utcoffset() is None:
        raise InvariantViolation("CANONICAL_D2_EVIDENCE_ATTESTATION_INVALID")
    return {
        "schema_version": _PAYLOAD_SCHEMA,
        "raw_response_id": raw_response_id,
        "raw_response_hash": response.response_hash,
        "source": response.source,
        "endpoint": response.endpoint,
        "http_method": response.http_method,
        "status_code": response.status_code,
        "schema_version_raw": response.schema_version,
        "requested_at": response.requested_at.astimezone(timezone.utc).isoformat(),
        "received_at": response.received_at.astimezone(timezone.utc).isoformat(),
        "attestor_id": attestor_id,
        "attestor_registry_snapshot_id": registry.snapshot_id,
        "attestor_registry_content_hash": registry.content_hash,
        "issued_at": issued_at.astimezone(timezone.utc).isoformat(),
    }


def require_external_evidence_attestation(*, conn: sqlite3.Connection, raw_response_id: str,
                                          response: RawApiResponse, cutoff: datetime,
                                          registry: RuntimeAttestorRegistry) -> None:
    """Verify a pre-existing receipt; this function never records an attestation."""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise InvariantViolation("CANONICAL_D2_EVIDENCE_ATTESTATION_INVALID")
    row = conn.execute(
        """SELECT attestation_id, attestor_id, payload_json, signature_base64, content_hash, issued_at_utc
           FROM am_external_evidence_attestation WHERE raw_response_id=?""", (raw_response_id,)
    ).fetchone()
    if row is None:
        raise DataQualityError("CANONICAL_D2_EVIDENCE_ATTESTATION_MISSING")
    try:
        payload = json.loads(str(row[2]))
        issued_at = _utc(row[5])
        signature = b64decode(str(row[3]), validate=True)
    except (TypeError, ValueError, Base64Error, json.JSONDecodeError) as exc:
        raise InvariantViolation("CANONICAL_D2_EVIDENCE_ATTESTATION_INVALID") from exc
    if (not isinstance(payload, Mapping) or not isinstance(row[1], str) or
            issued_at < registry.bound_at or issued_at < response.received_at.astimezone(timezone.utc) or issued_at > cutoff or
            response.received_at.astimezone(timezone.utc) > cutoff or
            payload != attestation_payload(raw_response_id=raw_response_id, response=response,
                                           attestor_id=str(row[1]), issued_at=issued_at, registry=registry) or
            str(row[4]) != digest(canonical({"payload": payload, "signature_base64": str(row[3])})) or
            str(row[0]) != str(row[4])):
        raise InvariantViolation("CANONICAL_D2_EVIDENCE_ATTESTATION_INVALID")
    rule = registry.attestors.get(str(row[1]))
    if rule is None or issued_at < rule[1] or (rule[2] is not None and issued_at >= rule[2]):
        raise DataQualityError("CANONICAL_D2_EVIDENCE_ATTESTATION_UNTRUSTED")
    try:
        Ed25519PublicKey.from_public_bytes(rule[0]).verify(signature, canonical(payload))
    except (ValueError, InvalidSignature) as exc:
        raise InvariantViolation("CANONICAL_D2_EVIDENCE_ATTESTATION_INVALID") from exc
