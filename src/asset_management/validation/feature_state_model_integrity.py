"""AMA-39 Feature, State, and Model Integrity acceptance gate."""
from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from types import MappingProxyType
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError, InvariantViolation
from .account_truth import AcceptanceDecision, CheckEvidence
from .canonical_d1_runtime_evidence import (
    CanonicalD1RuntimeEvidence, CanonicalD1RuntimeEvidenceRepository,
)
from .external_attestation import require_runtime_attestor_registry


REQUIRED_FEATURE_STATE_MODEL_CHECKS = (
    "FEATURE_PIT_LEAKAGE_BLOCKED",
    "HORIZON_VALIDITY_DECAY_CONTRACT_VERIFIED",
    "FOUR_STATE_SNAPSHOTS_REPRODUCIBLE",
    "CALCULATION_LINEAGE_TO_RAW_MANIFEST_VERIFIED",
    "QUALITY_FRESHNESS_CONFIDENCE_PROPAGATION_VERIFIED",
    "MODEL_APPROVED_SCOPE_ENFORCED",
    "IDENTICAL_INPUT_VERSION_STATE_MODEL_OUTPUT_HASH_REPRODUCIBLE",
)


_GIT_REVISION = re.compile(r"git:[0-9a-f]{40}")
_IMMUTABLE_EVIDENCE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_EVIDENCE_CATALOG_KIND = "feature-state-model-gate-evidence"
_D1_RUNTIME_ATTESTATION_SCHEMA = "canonical-d1-runtime-attestation@1"
_CANONICAL_STORE_URI = re.compile(r"gs://[a-z0-9](?:[a-z0-9._-]{1,220}[a-z0-9])?(?:/[^\s?#]*)?")


@dataclass(frozen=True, slots=True)
class FeatureStateModelSourceRevisionVerifier:
    """Resolve D1 source identity only from this checkout's current Git HEAD."""

    repository_root: Path
    git_executable: str = "git"

    def __post_init__(self) -> None:
        if (not isinstance(self.repository_root, Path) or not self.repository_root.is_dir() or
                not isinstance(self.git_executable, str) or not self.git_executable.strip()):
            raise InvariantViolation("FEATURE_STATE_MODEL_SOURCE_VERIFIER_INVALID")

    def verify(self, code_revision: str) -> tuple[str, str] | None:
        if _GIT_REVISION.fullmatch(code_revision) is None:
            return None
        commit = code_revision.removeprefix("git:")
        if (self._rev_parse(f"{commit}^{{commit}}") != commit or
                self._rev_parse("HEAD^{commit}") != commit):
            return None
        source_tree = self._rev_parse(f"{commit}^{{tree}}")
        return (code_revision, source_tree) if source_tree is not None else None

    def _rev_parse(self, revision: str) -> str | None:
        try:
            result = subprocess.run(
                [self.git_executable, "-C", str(self.repository_root), "rev-parse", "--verify", revision],
                check=False, capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = result.stdout.strip()
        return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


@dataclass(frozen=True, slots=True)
class CanonicalD1RuntimeAuthority:
    """A runtime-bound, independently signed canonical authority identity."""

    attestor_registry_snapshot_id: str
    attestor_registry_content_hash: str
    runtime_attestation_id: str
    runtime_attestation_hash: str
    canonical_store_uri: str

    def __post_init__(self) -> None:
        identifiers = (self.attestor_registry_snapshot_id, self.attestor_registry_content_hash,
                       self.runtime_attestation_id, self.runtime_attestation_hash)
        if (any(not isinstance(value, str) or _IMMUTABLE_EVIDENCE_ID.fullmatch(f"sha256:{value}") is None
                for value in identifiers) or not isinstance(self.canonical_store_uri, str) or
                _CANONICAL_STORE_URI.fullmatch(self.canonical_store_uri) is None):
            raise InvariantViolation("CANONICAL_D1_RUNTIME_AUTHORITY_INVALID")


def _runtime_attestation_payload(*, runtime_evidence: CanonicalD1RuntimeEvidence,
                                 attestor_id: str, issued_at: datetime,
                                 registry_snapshot_id: str, registry_content_hash: str,
                                 canonical_store_uri: str) -> dict[str, str]:
    return {
        "schema_version": _D1_RUNTIME_ATTESTATION_SCHEMA,
        "runtime_run_id": runtime_evidence.runtime_run_id,
        "runtime_code_revision": runtime_evidence.code_revision,
        "canonical_runtime_evidence_hash": runtime_evidence.content_hash,
        "canonical_runtime_catalog_object_id": runtime_evidence.catalog_object_id,
        "canonical_store_uri": canonical_store_uri,
        "attestor_id": attestor_id,
        "attestor_registry_snapshot_id": registry_snapshot_id,
        "attestor_registry_content_hash": registry_content_hash,
        "issued_at": issued_at.astimezone(timezone.utc).isoformat(),
    }


@dataclass(frozen=True, slots=True)
class CanonicalD1RuntimeAuthorityVerifier:
    """Verify the code-bound external trust root selected by this exact runtime.

    A caller-owned SQLite file or local immutable-store directory is never a
    canonical authority.  The runtime must instead already be bound, before
    its information cutoff, to the independently signed registry verified by
    ``external_attestation``.  This class has no write or signing API.
    """

    runtime_evidence_repository: CanonicalD1RuntimeEvidenceRepository

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_evidence_repository, CanonicalD1RuntimeEvidenceRepository):
            raise InvariantViolation("CANONICAL_D1_RUNTIME_AUTHORITY_VERIFIER_INVALID")

    def verify(self, *, runtime_evidence_repository: CanonicalD1RuntimeEvidenceRepository,
               runtime_evidence: CanonicalD1RuntimeEvidence) -> CanonicalD1RuntimeAuthority | None:
        if (runtime_evidence_repository is not self.runtime_evidence_repository or
                not isinstance(runtime_evidence, CanonicalD1RuntimeEvidence)):
            return None
        try:
            cutoff = runtime_evidence_repository.runtime_information_cutoff(
                runtime_run_id=runtime_evidence.runtime_run_id,
                code_revision=runtime_evidence.code_revision,
            )
            # The registry is verified against the immutable, code-bound Cloud
            # KMS public authority.  A separately signed D1 attestation then
            # binds it to this exact bundle and canonical store URI, so a
            # copied registry cannot be rebound in a caller-owned SQLite file.
            registry = require_runtime_attestor_registry(
                conn=runtime_evidence_repository.connection,
                runtime_run_id=runtime_evidence.runtime_run_id,
                cutoff=cutoff,
            )
            row = runtime_evidence_repository.connection.execute(
                """SELECT attestation_id, attestor_id, payload_json, signature_base64, content_hash,
                          issued_at_utc
                   FROM am_canonical_d1_runtime_attestation WHERE runtime_run_id=?""",
                (runtime_evidence.runtime_run_id,),
            ).fetchone()
            if row is None:
                raise DataQualityError("CANONICAL_D1_RUNTIME_ATTESTATION_MISSING")
            attestation_id, attestor_id, payload_raw, signature_raw, content_hash, issued_raw = row
            payload = json.loads(str(payload_raw))
            issued_at = datetime.fromisoformat(str(issued_raw).replace("Z", "+00:00"))
            if issued_at.tzinfo is None or issued_at.utcoffset() is None:
                raise InvariantViolation("CANONICAL_D1_RUNTIME_ATTESTATION_INVALID")
            issued_at = issued_at.astimezone(timezone.utc)
            signature = b64decode(str(signature_raw), validate=True)
            canonical_store_uri = payload.get("canonical_store_uri") if isinstance(payload, dict) else None
            if (not isinstance(canonical_store_uri, str) or issued_at < registry.bound_at or
                    issued_at > cutoff or payload != _runtime_attestation_payload(
                        runtime_evidence=runtime_evidence, attestor_id=str(attestor_id), issued_at=issued_at,
                        registry_snapshot_id=registry.snapshot_id, registry_content_hash=registry.content_hash,
                        canonical_store_uri=canonical_store_uri) or
                    str(content_hash) != digest(canonical({"payload": payload,
                                                           "signature_base64": str(signature_raw)})) or
                    str(attestation_id) != str(content_hash)):
                raise InvariantViolation("CANONICAL_D1_RUNTIME_ATTESTATION_INVALID")
            rule = registry.attestors.get(str(attestor_id))
            if rule is None or issued_at < rule[1] or (rule[2] is not None and issued_at >= rule[2]):
                raise DataQualityError("CANONICAL_D1_RUNTIME_ATTESTATION_UNTRUSTED")
            Ed25519PublicKey.from_public_bytes(rule[0]).verify(signature, canonical(payload))
            return CanonicalD1RuntimeAuthority(
                registry.snapshot_id, registry.content_hash, str(attestation_id), str(content_hash),
                canonical_store_uri,
            )
        except (Base64Error, DataQualityError, InvariantViolation, InvalidSignature,
                OSError, TypeError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError):
            return None


@dataclass(frozen=True, slots=True)
class FeatureStateModelIntegrityGateInput:
    evaluated_at: datetime
    runtime_run_id: str
    code_revision: str
    evidence_code_revision: str
    checks: Mapping[str, CheckEvidence]

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise InvariantViolation("FEATURE_STATE_MODEL_GATE_TIME_NOT_AWARE")
        if (not isinstance(self.runtime_run_id, str) or not self.runtime_run_id.strip() or
                not isinstance(self.code_revision, str) or not self.code_revision.strip() or
                not isinstance(self.evidence_code_revision, str) or not self.evidence_code_revision.strip() or
                set(self.checks) != set(REQUIRED_FEATURE_STATE_MODEL_CHECKS)):
            raise InvariantViolation("FEATURE_STATE_MODEL_GATE_CHECK_SET_INVALID")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))


@dataclass(frozen=True, slots=True)
class FeatureStateModelIntegrityGateResult:
    decision: AcceptanceDecision
    reason_codes: tuple[str, ...]
    evidence_artifact_ids: tuple[str, ...]
    evaluated_at: str
    runtime_run_id: str
    code_revision: str
    evidence_code_revision: str
    attestor_registry_snapshot_id: str | None
    attestor_registry_content_hash: str | None
    runtime_attestation_id: str | None
    runtime_attestation_hash: str | None
    canonical_store_uri: str | None
    content_hash: str


def _artifact_is_verified(*, store: ImmutableDatasetStore | None, artifact_id: str,
                          check_name: str, source: tuple[str, str] | None,
                          runtime_evidence: CanonicalD1RuntimeEvidence | None,
                          runtime_authority: CanonicalD1RuntimeAuthority | None) -> bool:
    if (store is None or source is None or runtime_evidence is None or runtime_authority is None or
            _IMMUTABLE_EVIDENCE_ID.fullmatch(artifact_id) is None):
        return False
    identifier = artifact_id.removeprefix("sha256:")
    try:
        content = store.layout.resolve(
            "catalog", f"{_EVIDENCE_CATALOG_KIND}/{identifier}.json").read_bytes()
        body = json.loads(content)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    expected = {
        "schema_version": "feature-state-model-gate-evidence@4",
        "check_name": check_name,
        "code_revision": source[0],
        "source_tree": source[1],
        "runtime_run_id": runtime_evidence.runtime_run_id,
        "runtime_code_revision": runtime_evidence.code_revision,
        "canonical_runtime_evidence_hash": runtime_evidence.content_hash,
        "canonical_runtime_catalog_object_id": runtime_evidence.catalog_object_id,
        "attestor_registry_snapshot_id": runtime_authority.attestor_registry_snapshot_id,
        "attestor_registry_content_hash": runtime_authority.attestor_registry_content_hash,
        "runtime_attestation_id": runtime_authority.runtime_attestation_id,
        "runtime_attestation_hash": runtime_authority.runtime_attestation_hash,
        "canonical_store_uri": runtime_authority.canonical_store_uri,
    }
    return content == canonical(expected) and digest(content) == identifier and body == expected


def evaluate_feature_state_model_integrity_gate(
        inputs: FeatureStateModelIntegrityGateInput, *,
        evidence_store: ImmutableDatasetStore | None = None,
        source_revision_verifier: FeatureStateModelSourceRevisionVerifier | None = None,
        runtime_evidence_repository: CanonicalD1RuntimeEvidenceRepository | None = None,
        runtime_authority_verifier: CanonicalD1RuntimeAuthorityVerifier | None = None,
        ) -> FeatureStateModelIntegrityGateResult:
    """Fail closed unless D1 evidence is immutable and bound to current source."""
    reasons: list[str] = []
    if _GIT_REVISION.fullmatch(inputs.code_revision) is None:
        reasons.append("CODE_REVISION_UNVERIFIED")
    if _GIT_REVISION.fullmatch(inputs.evidence_code_revision) is None:
        reasons.append("EVIDENCE_CODE_REVISION_UNVERIFIED")
    elif (_GIT_REVISION.fullmatch(inputs.code_revision) is not None and
          inputs.code_revision != inputs.evidence_code_revision):
        reasons.append("EVIDENCE_CODE_REVISION_MISMATCH")
    source = (source_revision_verifier.verify(inputs.code_revision)
              if isinstance(source_revision_verifier, FeatureStateModelSourceRevisionVerifier) else None)
    if source is None:
        reasons.append("SOURCE_REVISION_UNVERIFIED")
    if not isinstance(evidence_store, ImmutableDatasetStore):
        reasons.append("EVIDENCE_STORE_UNVERIFIED")
    runtime_evidence = None
    runtime_authority = None
    if not isinstance(runtime_evidence_repository, CanonicalD1RuntimeEvidenceRepository):
        reasons.append("RUNTIME_EVIDENCE_REPOSITORY_UNVERIFIED")
    elif isinstance(evidence_store, ImmutableDatasetStore):
        try:
            runtime_evidence = runtime_evidence_repository.replay(
                runtime_run_id=inputs.runtime_run_id, store=evidence_store)
        except Exception:
            reasons.append("RUNTIME_EVIDENCE_UNVERIFIED")
        else:
            if runtime_evidence.code_revision != inputs.code_revision:
                reasons.append("RUNTIME_EVIDENCE_CODE_REVISION_MISMATCH")
    if not isinstance(runtime_authority_verifier, CanonicalD1RuntimeAuthorityVerifier):
        reasons.append("CANONICAL_RUNTIME_AUTHORITY_UNVERIFIED")
    elif runtime_evidence is not None and isinstance(
            runtime_evidence_repository, CanonicalD1RuntimeEvidenceRepository):
        runtime_authority = runtime_authority_verifier.verify(
            runtime_evidence_repository=runtime_evidence_repository,
            runtime_evidence=runtime_evidence,
        )
        if runtime_authority is None:
            reasons.append("CANONICAL_RUNTIME_AUTHORITY_UNVERIFIED")
    else:
        reasons.append("CANONICAL_RUNTIME_AUTHORITY_UNVERIFIED")
    for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS:
        check = inputs.checks[name]
        if not check.passed:
            reasons.append(f"CHECK_FAILED:{name}")
        elif not all(_artifact_is_verified(
                store=evidence_store, artifact_id=artifact, check_name=name, source=source,
                runtime_evidence=runtime_evidence, runtime_authority=runtime_authority)
                     for artifact in check.artifact_ids):
            reasons.append(f"EVIDENCE_ARTIFACT_UNVERIFIED:{name}")
    reasons_tuple = tuple(reasons)
    decision = AcceptanceDecision.FAIL if reasons_tuple else AcceptanceDecision.PASS
    artifacts = tuple(sorted({artifact for check in inputs.checks.values()
                              for artifact in check.artifact_ids}))
    payload = {
        "decision": decision.value,
        "reason_codes": list(reasons_tuple),
        "evidence_artifact_ids": list(artifacts),
        "evaluated_at": inputs.evaluated_at.isoformat(),
        "runtime_run_id": inputs.runtime_run_id,
        "code_revision": inputs.code_revision,
        "evidence_code_revision": inputs.evidence_code_revision,
        "attestor_registry_snapshot_id": (runtime_authority.attestor_registry_snapshot_id
                                           if runtime_authority is not None else None),
        "attestor_registry_content_hash": (runtime_authority.attestor_registry_content_hash
                                            if runtime_authority is not None else None),
        "runtime_attestation_id": (runtime_authority.runtime_attestation_id
                                    if runtime_authority is not None else None),
        "runtime_attestation_hash": (runtime_authority.runtime_attestation_hash
                                      if runtime_authority is not None else None),
        "canonical_store_uri": (runtime_authority.canonical_store_uri
                                 if runtime_authority is not None else None),
    }
    return FeatureStateModelIntegrityGateResult(
        decision, reasons_tuple, artifacts, inputs.evaluated_at.isoformat(), inputs.runtime_run_id,
        inputs.code_revision,
        inputs.evidence_code_revision,
        (runtime_authority.attestor_registry_snapshot_id if runtime_authority is not None else None),
        (runtime_authority.attestor_registry_content_hash if runtime_authority is not None else None),
        (runtime_authority.runtime_attestation_id if runtime_authority is not None else None),
        (runtime_authority.runtime_attestation_hash if runtime_authority is not None else None),
        (runtime_authority.canonical_store_uri if runtime_authority is not None else None),
        digest(canonical(payload)),
    )
