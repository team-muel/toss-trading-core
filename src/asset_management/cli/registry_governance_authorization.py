"""Prepare and verify, but never sign, a Cloud KMS registry authorization.

This utility deliberately has no activation, database-write, Gate D2, or
canonical-run mode.  It emits canonical bytes for an owner to sign and can
independently verify the returned detached signature with the reviewed public
key.  The resulting local evidence is still not runtime authority until its
effective interval is reviewed into the authority history.
"""
from __future__ import annotations

import argparse
from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.validation import external_attestation as attestation


_REAL_AUTHORITY_ID = ("projects/toss-trading-core-lab-508411/locations/global/keyRings/"
                      "toss-governance/cryptoKeys/canonical-governance-authority/"
                      "cryptoKeyVersions/1")


def _utc(value: str) -> datetime:
    try:
        return attestation._utc(value)
    except ValueError as exc:
        raise ValueError("UTC timestamp with explicit offset is required") from exc


def _authority(authority_id: str) -> attestation.RegistryGovernanceAuthority:
    matches = [record for record in attestation._REGISTRY_GOVERNANCE_AUTHORITY_HISTORY
               if record.authority_id == authority_id]
    if len(matches) != 1:
        raise DataQualityError("CANONICAL_D2_ATTESTOR_REGISTRY_AUTHORITY_UNTRUSTED")
    attestation._authority_public_key(matches[0])
    return matches[0]


def _registry(path: Path) -> tuple[dict[str, object], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not attestation._registry(payload):
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
    return payload, digest(canonical(payload))


def _activation_candidate(*, authority: attestation.RegistryGovernanceAuthority,
                          effective_from: datetime) -> dict[str, object]:
    candidate = replace(authority, effective_from_utc=effective_from.isoformat(),
                        effective_to_utc=None, revoked_at_utc=None)
    return {
        "schema_version": "registry-governance-authority-activation-candidate@1",
        "authority": asdict(candidate),
        "authority_record_hash": digest(canonical(asdict(candidate))),
    }


def _write_once(path: Path, body: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite evidence candidate: {path}")
    path.write_bytes(body)


def prepare(*, registry_path: Path, output_dir: Path, authority_id: str,
            effective_from: str, published_at: str) -> dict[str, str]:
    """Write exact canonical authorization bytes; no Cloud KMS call is made."""
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    effective = _utc(effective_from)
    published = _utc(published_at)
    if published < effective:
        raise ValueError("published_at must not precede effective_from_utc")
    authority = _authority(authority_id)
    registry, snapshot_id = _registry(registry_path)
    authorization = attestation.registry_authorization_payload(
        authority_id=authority.authority_id, snapshot_id=snapshot_id,
        registry_hash=snapshot_id, published_at=published)
    authorization_bytes = canonical(authorization)
    candidate = _activation_candidate(authority=authority, effective_from=effective)
    output_dir.mkdir(parents=True)
    _write_once(output_dir / "attestor_registry_snapshot.json", canonical(registry))
    _write_once(output_dir / "registry_authorization.payload.json", authorization_bytes)
    _write_once(output_dir / "authority_activation_candidate.json", canonical(candidate))
    _write_once(output_dir / "registry_authorization.sha256", (digest(authorization_bytes) + "\n").encode())
    command = (
        "gcloud kms asymmetric-sign --project=toss-trading-core-lab-508411 --location=global "
        "--keyring=toss-governance --key=canonical-governance-authority --version=1 "
        f"--input-file={output_dir / 'registry_authorization.payload.json'} "
        f"--signature-file={output_dir / 'registry_authorization.signature.base64'}\n"
    )
    _write_once(output_dir / "SIGN_WITH_USER_OWNED_KMS.txt", command.encode())
    return {
        "status": "PREPARED_NOT_AUTHORIZED",
        "authority_id": authority.authority_id,
        "registry_hash": snapshot_id,
        "snapshot_id": snapshot_id,
        "authorization_payload_sha256": digest(authorization_bytes),
    }


def verify(*, registry_path: Path, authorization_path: Path, signature_path: Path,
           activation_candidate_path: Path, verified_evidence_dir: Path,
           authority_id: str) -> dict[str, str]:
    """Verify a user-produced detached signature without activating authority."""
    authority = _authority(authority_id)
    registry, snapshot_id = _registry(registry_path)
    raw_authorization = authorization_path.read_bytes()
    try:
        authorization = json.loads(raw_authorization)
        candidate = json.loads(activation_candidate_path.read_text(encoding="utf-8"))
        signature_text = signature_path.read_text(encoding="ascii").strip()
        signature = b64decode(signature_text, validate=True)
    except (UnicodeDecodeError, json.JSONDecodeError, Base64Error) as exc:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID") from exc
    if (not isinstance(authorization, Mapping) or not isinstance(candidate, Mapping) or
            not isinstance(candidate.get("authority"), Mapping) or
            raw_authorization != canonical(authorization)):
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
    try:
        published = _utc(str(authorization["published_at"]))
        effective_from = _utc(str(candidate["authority"]["effective_from_utc"]))
        expected = attestation.registry_authorization_payload(
            authority_id=authority.authority_id, snapshot_id=snapshot_id,
            registry_hash=snapshot_id, published_at=published)
    except (KeyError, ValueError, TypeError) as exc:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID") from exc
    if published < effective_from:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
    expected_candidate = _activation_candidate(
        authority=authority, effective_from=effective_from)
    if authorization != expected or candidate != expected_candidate:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(attestation._authority_public_key(authority)).verify(
            signature, raw_authorization)
    except (ValueError, InvalidSignature) as exc:
        raise InvariantViolation("CANONICAL_D2_ATTESTOR_REGISTRY_INVALID") from exc
    evidence = {
        "schema_version": "registry-governance-authority-signature-evidence@1",
        "status": "SIGNATURE_VALID_NOT_ACTIVATED",
        "authority_activation_candidate": candidate,
        "registry_authorization": authorization,
        "registry_hash": snapshot_id,
        "snapshot_id": snapshot_id,
        "authorization_payload_sha256": digest(raw_authorization),
        "signature_base64": signature_text,
        "signature_sha256": digest(signature),
    }
    evidence_bytes = canonical(evidence)
    evidence_hash = digest(evidence_bytes)
    verified_evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = verified_evidence_dir / f"{evidence_hash}.json"
    _write_once(evidence_path, evidence_bytes)
    return {
        "status": "SIGNATURE_VALID_NOT_ACTIVATED",
        "authority_id": authority.authority_id,
        "snapshot_id": snapshot_id,
        "registry_hash": snapshot_id,
        "authorization_payload_sha256": digest(raw_authorization),
        "signature_sha256": digest(signature),
        "authority_record_hash": str(candidate["authority_record_hash"]),
        "verified_evidence_sha256": evidence_hash,
        "verified_evidence_path": str(evidence_path),
    }


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepared = subparsers.add_parser("prepare")
    prepared.add_argument("--registry-json", type=Path, required=True)
    prepared.add_argument("--output-dir", type=Path, required=True)
    prepared.add_argument("--effective-from-utc", required=True)
    prepared.add_argument("--published-at", required=True)
    prepared.add_argument("--authority-id", default=_REAL_AUTHORITY_ID)
    verified = subparsers.add_parser("verify")
    verified.add_argument("--registry-json", type=Path, required=True)
    verified.add_argument("--authorization-file", type=Path, required=True)
    verified.add_argument("--signature-file", type=Path, required=True)
    verified.add_argument("--activation-candidate-file", type=Path, required=True)
    verified.add_argument("--verified-evidence-dir", type=Path, required=True,
                          help="new content-addressed, no-clobber verification evidence output")
    verified.add_argument("--authority-id", default=_REAL_AUTHORITY_ID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _arguments().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(registry_path=args.registry_json, output_dir=args.output_dir,
                             authority_id=args.authority_id, effective_from=args.effective_from_utc,
                             published_at=args.published_at)
        else:
            result = verify(registry_path=args.registry_json, authorization_path=args.authorization_file,
                            signature_path=args.signature_file,
                            activation_candidate_path=args.activation_candidate_file,
                            verified_evidence_dir=args.verified_evidence_dir,
                            authority_id=args.authority_id)
    except (OSError, ValueError, DataQualityError, InvariantViolation) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
