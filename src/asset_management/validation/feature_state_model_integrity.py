"""AMA-39 Feature, State, and Model Integrity acceptance gate."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation
from .account_truth import AcceptanceDecision, CheckEvidence
from .canonical_d1_runtime_evidence import (
    CanonicalD1RuntimeEvidence, CanonicalD1RuntimeEvidenceRepository,
)


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
    content_hash: str


def _artifact_is_verified(*, store: ImmutableDatasetStore | None, artifact_id: str,
                          check_name: str, source: tuple[str, str] | None,
                          runtime_evidence: CanonicalD1RuntimeEvidence | None) -> bool:
    if (store is None or source is None or runtime_evidence is None or
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
        "schema_version": "feature-state-model-gate-evidence@2",
        "check_name": check_name,
        "code_revision": source[0],
        "source_tree": source[1],
        "runtime_run_id": runtime_evidence.runtime_run_id,
        "runtime_code_revision": runtime_evidence.code_revision,
        "canonical_runtime_evidence_hash": runtime_evidence.content_hash,
        "canonical_runtime_catalog_object_id": runtime_evidence.catalog_object_id,
    }
    return content == canonical(expected) and digest(content) == identifier and body == expected


def evaluate_feature_state_model_integrity_gate(
        inputs: FeatureStateModelIntegrityGateInput, *,
        evidence_store: ImmutableDatasetStore | None = None,
        source_revision_verifier: FeatureStateModelSourceRevisionVerifier | None = None,
        runtime_evidence_repository: CanonicalD1RuntimeEvidenceRepository | None = None,
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
    for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS:
        check = inputs.checks[name]
        if not check.passed:
            reasons.append(f"CHECK_FAILED:{name}")
        elif not all(_artifact_is_verified(
                store=evidence_store, artifact_id=artifact, check_name=name, source=source,
                runtime_evidence=runtime_evidence)
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
    }
    return FeatureStateModelIntegrityGateResult(
        decision, reasons_tuple, artifacts, inputs.evaluated_at.isoformat(), inputs.runtime_run_id,
        inputs.code_revision,
        inputs.evidence_code_revision, digest(canonical(payload)),
    )
