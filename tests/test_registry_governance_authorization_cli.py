from base64 import b64encode
from hashlib import sha256
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from asset_management.cli import registry_governance_authorization as cli
from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.validation import external_attestation as attestation


def _authority(key):
    der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    resource = "projects/test/locations/global/keyRings/ring/cryptoKeys/key/cryptoKeyVersions/1"
    return attestation.RegistryGovernanceAuthority(
        authority_id=resource, kms_key_version_resource=resource, signing_algorithm="EC_SIGN_ED25519",
        public_key_der_spki_base64=b64encode(der).decode(),
        public_key_fingerprint_sha256=sha256(der).hexdigest(),
        effective_from_utc=None, effective_to_utc=None)


def _registry(path, attestor):
    path.write_text(json.dumps({
        "schema_version": "canonical-evidence-attestor-registry@1",
        "attestors": [{
            "attestor_id": "provider-attestor", "algorithm": "ed25519",
            "public_key_base64": b64encode(attestor.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode(),
            "effective_from_utc": "2026-09-13T00:00:00+00:00", "effective_to_utc": None,
        }],
    }), encoding="utf-8")


def test_prepare_and_verify_user_signed_authorization_without_activating_authority(monkeypatch, tmp_path):
    governance_key = Ed25519PrivateKey.generate()
    authority = _authority(governance_key)
    monkeypatch.setattr(attestation, "_REGISTRY_GOVERNANCE_AUTHORITY_HISTORY", (authority,))
    registry_path = tmp_path / "registry.json"
    _registry(registry_path, Ed25519PrivateKey.generate())
    output_dir = tmp_path / "prepared"

    prepared = cli.prepare(
        registry_path=registry_path, output_dir=output_dir, authority_id=authority.authority_id,
        effective_from="2026-09-13T05:40:00+00:00", published_at="2026-09-13T05:40:00+00:00")

    authorization_path = output_dir / "registry_authorization.payload.json"
    authorization = authorization_path.read_bytes()
    assert prepared["status"] == "PREPARED_NOT_AUTHORIZED"
    assert prepared["snapshot_id"] == digest(canonical(json.loads(registry_path.read_text(encoding="utf-8"))))
    assert prepared["authorization_payload_sha256"] == digest(authorization)
    assert "--digest-algorithm" not in (output_dir / "SIGN_WITH_USER_OWNED_KMS.txt").read_text(encoding="utf-8")

    signature_path = output_dir / "registry_authorization.signature.base64"
    signature_path.write_text(b64encode(governance_key.sign(authorization)).decode(), encoding="ascii")
    verified = cli.verify(
        registry_path=registry_path, authorization_path=authorization_path, signature_path=signature_path,
        activation_candidate_path=output_dir / "authority_activation_candidate.json",
        verified_evidence_dir=tmp_path / "verified-evidence",
        authority_id=authority.authority_id)

    assert verified["status"] == "SIGNATURE_VALID_NOT_ACTIVATED"
    assert verified["authority_id"] == authority.authority_id
    evidence_path = tmp_path / "verified-evidence" / f"{verified['verified_evidence_sha256']}.json"
    assert evidence_path.exists()
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["status"] == "SIGNATURE_VALID_NOT_ACTIVATED"
    assert attestation._REGISTRY_GOVERNANCE_AUTHORITY_HISTORY[0].effective_from_utc is None


def test_prepare_rejects_empty_or_unapproved_attestor_registry(monkeypatch, tmp_path):
    authority = _authority(Ed25519PrivateKey.generate())
    monkeypatch.setattr(attestation, "_REGISTRY_GOVERNANCE_AUTHORITY_HISTORY", (authority,))
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"schema_version": "canonical-evidence-attestor-registry@1", "attestors": []}),
                             encoding="utf-8")

    with pytest.raises(InvariantViolation, match="CANONICAL_D2_ATTESTOR_REGISTRY_INVALID"):
        cli.prepare(registry_path=registry_path, output_dir=tmp_path / "prepared",
                    authority_id=authority.authority_id, effective_from="2026-09-13T05:40:00+00:00",
                    published_at="2026-09-13T05:40:00+00:00")
