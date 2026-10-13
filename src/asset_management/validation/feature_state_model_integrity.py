"""AMA-39 Feature, State, and Model Integrity acceptance gate."""
from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
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
    _trusted_immutable_store,
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
_D1_RUNTIME_ATTESTATION_SCHEMA = "canonical-d1-runtime-attestation@3"
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
        try:
            index = subprocess.run(
                [self.git_executable, "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                 "-C", str(self.repository_root),
                 "--work-tree", str(self.repository_root.resolve()), "ls-files", "-v", "-z"],
                check=False, capture_output=True, timeout=5, env=self._git_environment(),
            )
            if (index.returncode != 0 or any(
                    entry[:1] == b"S" or entry[:1].islower()
                    for entry in index.stdout.split(b"\0") if entry)):
                return None
            status = subprocess.run(
                [self.git_executable, "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                 "-C", str(self.repository_root),
                 "--work-tree", str(self.repository_root.resolve()), "status",
                 "--porcelain=v1", "--untracked-files=all"],
                check=False, capture_output=True, text=True, timeout=5, env=self._git_environment(),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if status.returncode != 0 or status.stdout:
            return None
        source_tree = self._rev_parse(f"{commit}^{{tree}}")
        return (code_revision, source_tree) if source_tree is not None else None

    def _rev_parse(self, revision: str) -> str | None:
        try:
            result = subprocess.run(
                [self.git_executable, "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                 "-C", str(self.repository_root),
                 "--work-tree", str(self.repository_root.resolve()), "rev-parse", "--verify", revision],
                check=False, capture_output=True, text=True, timeout=5, env=self._git_environment(),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = result.stdout.strip()
        return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None

    @staticmethod
    def _git_environment() -> dict[str, str]:
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith("GIT_")}
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        return environment


@dataclass(frozen=True, slots=True)
class SourceGitExecutablePin:
    """Executable identity approved inside the independently signed attestation."""

    executable_path: str
    content_sha256: str

    def __post_init__(self) -> None:
        if (type(self.executable_path) is not str or
                any(ord(character) < 32 for character in self.executable_path) or
                not Path(self.executable_path).is_absolute() or
                type(self.content_sha256) is not str or
                _IMMUTABLE_EVIDENCE_ID.fullmatch(f"sha256:{self.content_sha256}") is None):
            raise InvariantViolation("CANONICAL_D1_GIT_PIN_INVALID")

    def verified_executable(self) -> str | None:
        path = Path(self.executable_path)
        try:
            resolved = path.resolve(strict=True)
            if (path.is_symlink() or str(resolved) != self.executable_path or
                    not resolved.is_file() or digest(resolved.read_bytes()) != self.content_sha256):
                return None
            return str(resolved)
        except OSError:
            return None


def _executing_source_identity(code_revision: str,
                               requested_verifier: FeatureStateModelSourceRevisionVerifier | None,
                               *, git_pin: SourceGitExecutablePin | None = None) -> tuple[str, str] | None:
    """Bind source identity to this executing module, never a caller checkout."""
    repository_root = Path(__file__).resolve().parents[3]
    if requested_verifier is not None and (
            type(requested_verifier) is not FeatureStateModelSourceRevisionVerifier or
            requested_verifier.repository_root.resolve() != repository_root or
            requested_verifier.git_executable != "git"):
        return None
    if type(git_pin) is not SourceGitExecutablePin:
        return None
    git_executable = SourceGitExecutablePin.verified_executable(git_pin)
    if git_executable is None:
        return None
    verifier = FeatureStateModelSourceRevisionVerifier(repository_root, git_executable)
    return FeatureStateModelSourceRevisionVerifier.verify(verifier, code_revision)


@dataclass(frozen=True, slots=True)
class CanonicalD1RuntimeAuthority:
    """A runtime-bound, independently signed canonical authority identity."""

    attestor_registry_snapshot_id: str
    attestor_registry_content_hash: str
    runtime_attestation_id: str
    runtime_attestation_hash: str
    canonical_store_uri: str
    semantic_checks: Mapping[str, CheckEvidence]
    source_git_pin: SourceGitExecutablePin

    def __post_init__(self) -> None:
        identifiers = (self.attestor_registry_snapshot_id, self.attestor_registry_content_hash,
                       self.runtime_attestation_id, self.runtime_attestation_hash)
        if (any(not isinstance(value, str) or _IMMUTABLE_EVIDENCE_ID.fullmatch(f"sha256:{value}") is None
                for value in identifiers) or not isinstance(self.canonical_store_uri, str) or
                _CANONICAL_STORE_URI.fullmatch(self.canonical_store_uri) is None):
            raise InvariantViolation("CANONICAL_D1_RUNTIME_AUTHORITY_INVALID")
        _signed_checks_payload(self.semantic_checks)
        if type(self.source_git_pin) is not SourceGitExecutablePin:
            raise InvariantViolation("CANONICAL_D1_GIT_PIN_INVALID")
        object.__setattr__(self, "semantic_checks", MappingProxyType(dict(self.semantic_checks)))


def _signed_checks_payload(checks: Mapping[str, CheckEvidence]) -> dict[str, object]:
    if (not isinstance(checks, Mapping) or set(checks) != set(REQUIRED_FEATURE_STATE_MODEL_CHECKS)
            or any(not isinstance(check, CheckEvidence) or
                   any(_IMMUTABLE_EVIDENCE_ID.fullmatch(artifact) is None
                       for artifact in check.artifact_ids) for check in checks.values())):
        raise InvariantViolation("CANONICAL_D1_SEMANTIC_CHECKS_INVALID")
    return {name: {"passed": checks[name].passed,
                   "artifact_ids": list(checks[name].artifact_ids)}
            for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS}


def _parse_signed_checks(value: object) -> Mapping[str, CheckEvidence]:
    if not isinstance(value, dict) or set(value) != set(REQUIRED_FEATURE_STATE_MODEL_CHECKS):
        raise InvariantViolation("CANONICAL_D1_SEMANTIC_CHECKS_INVALID")
    checks = {}
    for name, entry in value.items():
        if (not isinstance(entry, dict) or set(entry) != {"passed", "artifact_ids"}
                or not isinstance(entry["artifact_ids"], list)
                or any(not isinstance(item, str) for item in entry["artifact_ids"])):
            raise InvariantViolation("CANONICAL_D1_SEMANTIC_CHECKS_INVALID")
        checks[name] = CheckEvidence(entry["passed"], tuple(entry["artifact_ids"]))
    if _signed_checks_payload(checks) != value:
        raise InvariantViolation("CANONICAL_D1_SEMANTIC_CHECKS_INVALID")
    return checks


def _runtime_attestation_payload(*, runtime_evidence: CanonicalD1RuntimeEvidence,
                                 attestor_id: str, issued_at: datetime,
                                 registry_snapshot_id: str, registry_content_hash: str,
                                 canonical_store_uri: str,
                                 semantic_checks: Mapping[str, CheckEvidence],
                                 source_git_pin: SourceGitExecutablePin) -> dict[str, object]:
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
        "semantic_checks": _signed_checks_payload(semantic_checks),
        "source_git_pin": {"executable_path": source_git_pin.executable_path,
                           "content_sha256": source_git_pin.content_sha256},
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
        if type(self.runtime_evidence_repository) is not CanonicalD1RuntimeEvidenceRepository:
            raise InvariantViolation("CANONICAL_D1_RUNTIME_AUTHORITY_VERIFIER_INVALID")

    def verify(self, *, runtime_evidence_repository: CanonicalD1RuntimeEvidenceRepository,
               runtime_evidence: CanonicalD1RuntimeEvidence) -> CanonicalD1RuntimeAuthority | None:
        if (type(runtime_evidence_repository) is not CanonicalD1RuntimeEvidenceRepository or
                runtime_evidence_repository is not self.runtime_evidence_repository or
                not isinstance(runtime_evidence, CanonicalD1RuntimeEvidence)):
            return None
        try:
            cutoff = CanonicalD1RuntimeEvidenceRepository.runtime_information_cutoff(
                runtime_evidence_repository,
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
            semantic_checks = _parse_signed_checks(
                payload.get("semantic_checks") if isinstance(payload, dict) else None)
            pin_payload = payload.get("source_git_pin") if isinstance(payload, dict) else None
            if not isinstance(pin_payload, dict) or set(pin_payload) != {"executable_path", "content_sha256"}:
                raise InvariantViolation("CANONICAL_D1_GIT_PIN_INVALID")
            source_git_pin = SourceGitExecutablePin(**pin_payload)
            if (not isinstance(canonical_store_uri, str) or issued_at < registry.bound_at or
                    issued_at > cutoff or payload != _runtime_attestation_payload(
                        runtime_evidence=runtime_evidence, attestor_id=str(attestor_id), issued_at=issued_at,
                        registry_snapshot_id=registry.snapshot_id, registry_content_hash=registry.content_hash,
                        canonical_store_uri=canonical_store_uri, semantic_checks=semantic_checks,
                        source_git_pin=source_git_pin) or
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
                canonical_store_uri, semantic_checks, source_git_pin,
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
    signed_check = runtime_authority.semantic_checks.get(check_name)
    if signed_check is None or not signed_check.passed or artifact_id not in signed_check.artifact_ids:
        return False
    identifier = artifact_id.removeprefix("sha256:")
    try:
        content = store.layout.resolve(
            "catalog", f"{_EVIDENCE_CATALOG_KIND}/{identifier}.json").read_bytes()
        body = json.loads(content)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    expected = {
        "schema_version": "feature-state-model-gate-evidence@5",
        "check_name": check_name,
        "passed": True,
        "code_revision": source[0],
        "source_tree": source[1],
        "runtime_run_id": runtime_evidence.runtime_run_id,
        "runtime_code_revision": runtime_evidence.code_revision,
        "canonical_runtime_evidence_hash": runtime_evidence.content_hash,
        "canonical_runtime_catalog_object_id": runtime_evidence.catalog_object_id,
        "attestor_registry_snapshot_id": runtime_authority.attestor_registry_snapshot_id,
        "attestor_registry_content_hash": runtime_authority.attestor_registry_content_hash,
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
    try:
        evidence_store = _trusted_immutable_store(evidence_store)
    except InvariantViolation:
        evidence_store = None
    if _GIT_REVISION.fullmatch(inputs.code_revision) is None:
        reasons.append("CODE_REVISION_UNVERIFIED")
    if _GIT_REVISION.fullmatch(inputs.evidence_code_revision) is None:
        reasons.append("EVIDENCE_CODE_REVISION_UNVERIFIED")
    elif (_GIT_REVISION.fullmatch(inputs.code_revision) is not None and
          inputs.code_revision != inputs.evidence_code_revision):
        reasons.append("EVIDENCE_CODE_REVISION_MISMATCH")
    if not isinstance(evidence_store, ImmutableDatasetStore):
        reasons.append("EVIDENCE_STORE_UNVERIFIED")
    runtime_evidence = None
    runtime_authority = None
    if type(runtime_evidence_repository) is not CanonicalD1RuntimeEvidenceRepository:
        reasons.append("RUNTIME_EVIDENCE_REPOSITORY_UNVERIFIED")
    elif isinstance(evidence_store, ImmutableDatasetStore):
        try:
            runtime_evidence = CanonicalD1RuntimeEvidenceRepository.replay(
                runtime_evidence_repository,
                runtime_run_id=inputs.runtime_run_id, store=evidence_store)
        except Exception:
            reasons.append("RUNTIME_EVIDENCE_UNVERIFIED")
        else:
            if runtime_evidence.code_revision != inputs.code_revision:
                reasons.append("RUNTIME_EVIDENCE_CODE_REVISION_MISMATCH")
    if type(runtime_authority_verifier) is not CanonicalD1RuntimeAuthorityVerifier:
        reasons.append("CANONICAL_RUNTIME_AUTHORITY_UNVERIFIED")
    elif (runtime_evidence is not None and
          type(runtime_evidence_repository) is CanonicalD1RuntimeEvidenceRepository):
        runtime_authority = CanonicalD1RuntimeAuthorityVerifier.verify(
            runtime_authority_verifier,
            runtime_evidence_repository=runtime_evidence_repository,
            runtime_evidence=runtime_evidence,
        )
        if runtime_authority is None:
            reasons.append("CANONICAL_RUNTIME_AUTHORITY_UNVERIFIED")
    else:
        reasons.append("CANONICAL_RUNTIME_AUTHORITY_UNVERIFIED")
    source = (_executing_source_identity(inputs.code_revision, source_revision_verifier,
                                         git_pin=runtime_authority.source_git_pin)
              if runtime_authority is not None else None)
    if source is None:
        reasons.append("SOURCE_REVISION_UNVERIFIED")
    for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS:
        check = inputs.checks[name]
        if not check.passed:
            reasons.append(f"CHECK_FAILED:{name}")
        else:
            if runtime_authority is None or check != runtime_authority.semantic_checks[name]:
                reasons.append(f"SIGNED_CHECK_MISMATCH:{name}")
        if check.passed and not all(_artifact_is_verified(
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
