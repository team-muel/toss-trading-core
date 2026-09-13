"""Verification-only boundary for independently signed evidence receipts."""
from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_der_public_key

from asset_management.data.immutable import canonical, digest
from asset_management.data.raw_store import RawApiResponse
from asset_management.domain.errors import DataQualityError, InvariantViolation


_SCHEMA = "canonical-evidence-attestor-registry@1"
_REGISTRY_AUTHORIZATION_SCHEMA = "canonical-evidence-attestor-registry-authorization@1"
_PAYLOAD_SCHEMA = "external-evidence-attestation@1"

@dataclass(frozen=True, slots=True)
class RegistryGovernanceAuthority:
    """A reviewed, version-specific Cloud KMS registry-signing authority.

    This is deliberately code-bound rather than deployment configuration.  A
    rotation appends another immutable record in a reviewed change; it never
    replaces historical material.  A record with no effective interval is only
    registered public material, not an authority a runtime may use.
    """

    authority_id: str
    kms_key_version_resource: str
    signing_algorithm: str
    public_key_der_spki_base64: str
    public_key_fingerprint_sha256: str
    effective_from_utc: str | None
    effective_to_utc: str | None
    revoked_at_utc: str | None = None


# This public material was retrieved from the user-created Cloud KMS key
# version.  Its authority interval and registry authorization deliberately
# remain absent until independently supplied by the owner, so it cannot make a
# canonical run trust a registry by itself.  There is no private key, signing
# capability, or authority creation path in this repository or CI.
_REGISTRY_GOVERNANCE_AUTHORITY_HISTORY: tuple[RegistryGovernanceAuthority, ...] = (
    RegistryGovernanceAuthority(
        authority_id=("projects/toss-trading-core-lab-508411/locations/global/keyRings/"
                      "toss-governance/cryptoKeys/canonical-governance-authority/"
                      "cryptoKeyVersions/1"),
        kms_key_version_resource=("projects/toss-trading-core-lab-508411/locations/global/keyRings/"
                                  "toss-governance/cryptoKeys/canonical-governance-authority/"
                                  "cryptoKeyVersions/1"),
        signing_algorithm="EC_SIGN_ED25519",
        public_key_der_spki_base64="MCowBQYDK2VwAyEAvqj5Z/4W4MJN/qVPs+qv+1mHdP4WaNmRyeRMvsX9lO0=",
        public_key_fingerprint_sha256="e49ea0d6ae429017125ecdb2cae298bf5d82ae5d1b6a565b9a95d4ab6bc71084",
        effective_from_utc=None,
        effective_to_utc=None,
    ),
)


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _optional_utc(value: object) -> datetime | None:
    return None if value is None else _utc(value)


def _authority_public_key(authority: RegistryGovernanceAuthority) -> bytes:
    """Return only a fingerprint-verified Ed25519 public key, never key material from a caller."""
    if (not isinstance(authority.authority_id, str) or not authority.authority_id.strip() or
            authority.authority_id != authority.kms_key_version_resource or
            authority.signing_algorithm != "EC_SIGN_ED25519" or
            not isinstance(authority.public_key_fingerprint_sha256, str) or
            len(authority.public_key_fingerprint_sha256) != 64 or
            any(char not in "0123456789abcdef" for char in authority.public_key_fingerprint_sha256)):
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID")
    try:
        der = b64decode(authority.public_key_der_spki_base64, validate=True)
        key = load_der_public_key(der)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError
        raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    except (TypeError, ValueError, Base64Error) as exc:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID") from exc
    if sha256(der).hexdigest() != authority.public_key_fingerprint_sha256:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID")
    return raw


def _select_registry_authority(*, authority_id: str, published_at: datetime,
                               cutoff: datetime) -> bytes:
    """Select exactly one immutable authority valid at publication and runtime cutoff."""
    if (not isinstance(authority_id, str) or not authority_id.strip() or
            published_at.tzinfo is None or published_at.utcoffset() is None or
            cutoff.tzinfo is None or cutoff.utcoffset() is None):
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID")
    if published_at.astimezone(timezone.utc) > cutoff.astimezone(timezone.utc):
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID")
    if not isinstance(_REGISTRY_GOVERNANCE_AUTHORITY_HISTORY, tuple):
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID")
    published_candidates: list[tuple[RegistryGovernanceAuthority, bytes]] = []
    cutoff_candidates: list[tuple[RegistryGovernanceAuthority, bytes]] = []
    seen_resources: set[str] = set()
    for record in _REGISTRY_GOVERNANCE_AUTHORITY_HISTORY:
        if not isinstance(record, RegistryGovernanceAuthority):
            raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID")
        key = _authority_public_key(record)
        try:
            effective_from = _optional_utc(record.effective_from_utc)
            effective_to = _optional_utc(record.effective_to_utc)
            revoked_at = _optional_utc(record.revoked_at_utc)
        except ValueError as exc:
            raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID") from exc
        if record.kms_key_version_resource in seen_resources:
            raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID")
        seen_resources.add(record.kms_key_version_resource)
        if effective_from is None:
            if effective_to is not None or revoked_at is not None:
                raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID")
            continue
        if ((effective_to is not None and effective_to <= effective_from) or
                (revoked_at is not None and revoked_at <= effective_from)):
            raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_INVALID")

        def active_at(instant: datetime) -> bool:
            return (instant >= effective_from and
                    (effective_to is None or instant < effective_to) and
                    (revoked_at is None or instant < revoked_at))

        if active_at(published_at.astimezone(timezone.utc)):
            published_candidates.append((record, key))
        if active_at(cutoff.astimezone(timezone.utc)):
            cutoff_candidates.append((record, key))

    def require_single(candidates: list[tuple[RegistryGovernanceAuthority, bytes]]) -> tuple[RegistryGovernanceAuthority, bytes]:
        if len(candidates) == 1:
            return candidates[0]
        reason = ("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_AMBIGUOUS"
                  if len(candidates) > 1 else "CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_UNTRUSTED")
        raise DataQualityError(reason)

    published_record, _ = require_single(published_candidates)
    cutoff_record, key = require_single(cutoff_candidates)
    if (published_record.kms_key_version_resource != cutoff_record.kms_key_version_resource or
            cutoff_record.authority_id != authority_id):
        raise DataQualityError("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_UNTRUSTED")
    return key


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
    authority_key = _select_registry_authority(
        authority_id=str(authority_id), published_at=published, cutoff=cutoff_time)
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
