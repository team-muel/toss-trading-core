"""AMA-108 Signal and Forecast Integrity acceptance gate."""
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


REQUIRED_SIGNAL_FORECAST_CHECKS = (
    "FEATURE_SIGNAL_SEMANTIC_SEPARATION_VERIFIED",
    "SIGNAL_CONTRACT_LINEAGE_HORIZON_VALIDITY_VERIFIED",
    "CROSS_SECTIONAL_SIGNAL_DIAGNOSTICS_VERIFIED",
    "ETF_TIME_SERIES_OOS_CALIBRATION_UTILITY_VERIFIED",
    "PIT_NORMALIZATION_NEUTRALIZATION_VERIFIED",
    "SIGNAL_REDUNDANCY_FACTOR_INCREMENTAL_POWER_VERIFIED",
    "SIGNAL_FORECAST_OOS_UNCERTAINTY_LINEAGE_VERIFIED",
    "FORECAST_COMBINATION_DIVERSIFICATION_COST_STABILITY_VERIFIED",
    "STRATEGY_VERSION_REFERENCES_VERIFIED",
    "WEAK_SIGNAL_SHRINK_OR_ABSTAIN_VERIFIED",
    "LOOK_AHEAD_SURVIVORSHIP_LEAKAGE_BLOCKED",
)


_GIT_REVISION = re.compile(r"git:[0-9a-f]{40}")
_IMMUTABLE_EVIDENCE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_EVIDENCE_CATALOG_KIND = "signal-forecast-gate-evidence"


@dataclass(frozen=True, slots=True)
class VerifiedSourceRevision:
    """A Git commit and its immutable tree, resolved from a trusted checkout."""

    code_revision: str
    source_tree: str


@dataclass(frozen=True, slots=True)
class GitSourceRevisionVerifier:
    """Resolve acceptance source identity from a local Git object database.

    The gate never accepts a caller-provided SHA merely because it has the
    expected shape: the value must resolve to this checkout's current HEAD and
    its tree. This verifier is deliberately read-only; it does not fetch,
    checkout, or alter the repository.
    """

    repository_root: Path
    git_executable: str = "git"

    def __post_init__(self) -> None:
        if (not isinstance(self.repository_root, Path) or not self.repository_root.is_dir() or
                not isinstance(self.git_executable, str) or not self.git_executable.strip()):
            raise InvariantViolation("SIGNAL_FORECAST_SOURCE_VERIFIER_INVALID")

    def verify(self, code_revision: str) -> VerifiedSourceRevision | None:
        if _GIT_REVISION.fullmatch(code_revision) is None:
            return None
        commit = code_revision.removeprefix("git:")
        resolved_commit = self._rev_parse(f"{commit}^{{commit}}")
        trusted_head = self._rev_parse("HEAD^{commit}")
        if resolved_commit != commit or trusted_head != commit:
            return None
        source_tree = self._rev_parse(f"{commit}^{{tree}}")
        if source_tree is None or not re.fullmatch(r"[0-9a-f]{40}", source_tree):
            return None
        return VerifiedSourceRevision(code_revision, source_tree)

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
class SignalForecastIntegrityGateInput:
    evaluated_at: datetime
    code_revision: str
    evidence_code_revision: str
    checks: Mapping[str, CheckEvidence]

    def __post_init__(self) -> None:
        if (not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None or
                self.evaluated_at.utcoffset() is None):
            raise InvariantViolation("SIGNAL_FORECAST_GATE_TIME_NOT_AWARE")
        if (not isinstance(self.code_revision, str) or not self.code_revision.strip() or
                not isinstance(self.evidence_code_revision, str) or
                not self.evidence_code_revision.strip() or
                not isinstance(self.checks, Mapping) or
                set(self.checks) != set(REQUIRED_SIGNAL_FORECAST_CHECKS) or
                any(not isinstance(check, CheckEvidence) for check in self.checks.values())):
            raise InvariantViolation("SIGNAL_FORECAST_GATE_CHECK_SET_INVALID")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))


@dataclass(frozen=True, slots=True)
class SignalForecastIntegrityGateResult:
    decision: AcceptanceDecision
    reason_codes: tuple[str, ...]
    evidence_artifact_ids: tuple[str, ...]
    evaluated_at: str
    code_revision: str
    evidence_code_revision: str
    content_hash: str

    @property
    def permits_m4_execution(self) -> bool:
        """Only a complete signal-and-forecast evidence set can cross into M4."""
        return self.decision is AcceptanceDecision.PASS


def _artifact_is_verified(*, store: ImmutableDatasetStore | None, artifact_id: str,
                          check_name: str, source: VerifiedSourceRevision | None) -> bool:
    """Read and bind one immutable catalog object without trusting its identifier."""
    if store is None or source is None or _IMMUTABLE_EVIDENCE_ID.fullmatch(artifact_id) is None:
        return False
    identifier = artifact_id.removeprefix("sha256:")
    try:
        content = store.layout.resolve(
            "catalog", f"{_EVIDENCE_CATALOG_KIND}/{identifier}.json").read_bytes()
        body = json.loads(content)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    expected = {
        "schema_version": "signal-forecast-gate-evidence@1",
        "check_name": check_name,
        "code_revision": source.code_revision,
        "source_tree": source.source_tree,
    }
    return content == canonical(expected) and digest(content) == identifier and body == expected


def evaluate_signal_forecast_integrity_gate(
        inputs: SignalForecastIntegrityGateInput, *,
        evidence_store: ImmutableDatasetStore | None = None,
        source_revision_verifier: GitSourceRevisionVerifier | None = None) -> SignalForecastIntegrityGateResult:
    """Fail closed unless evidence is immutable and bound to the evaluated source.

    A test selector or a short Git SHA is useful diagnostic context, but is not an
    immutable acceptance artifact.  In particular, it must not let an historical
    successful CI run authorize M4 after the implementation was reconciled or
    changed.  The caller is responsible for supplying the immutable evidence
    object IDs and the exact source revision captured by that evidence producer.
    """
    reasons: list[str] = []
    if _GIT_REVISION.fullmatch(inputs.code_revision) is None:
        reasons.append("CODE_REVISION_UNVERIFIED")
    if _GIT_REVISION.fullmatch(inputs.evidence_code_revision) is None:
        reasons.append("EVIDENCE_CODE_REVISION_UNVERIFIED")
    elif (_GIT_REVISION.fullmatch(inputs.code_revision) is not None and
          inputs.code_revision != inputs.evidence_code_revision):
        reasons.append("EVIDENCE_CODE_REVISION_MISMATCH")
    source = (source_revision_verifier.verify(inputs.code_revision)
              if isinstance(source_revision_verifier, GitSourceRevisionVerifier) else None)
    if source is None:
        reasons.append("SOURCE_REVISION_UNVERIFIED")
    if not isinstance(evidence_store, ImmutableDatasetStore):
        reasons.append("EVIDENCE_STORE_UNVERIFIED")
    for name in REQUIRED_SIGNAL_FORECAST_CHECKS:
        check = inputs.checks[name]
        if not check.passed:
            reasons.append(f"CHECK_FAILED:{name}")
        elif not all(_artifact_is_verified(
                store=evidence_store, artifact_id=artifact, check_name=name,
                source=source)
                      for artifact in check.artifact_ids):
            reasons.append(f"EVIDENCE_ARTIFACT_UNVERIFIED:{name}")
    reasons_tuple = tuple(reasons)
    decision = AcceptanceDecision.FAIL if reasons_tuple else AcceptanceDecision.PASS
    artifacts = tuple(sorted({artifact for check in inputs.checks.values()
                              for artifact in check.artifact_ids}))
    payload = {
        "decision": decision.value,
        "reason_codes": list(reasons),
        "evidence_artifact_ids": list(artifacts),
        "evaluated_at": inputs.evaluated_at.isoformat(),
        "code_revision": inputs.code_revision,
        "evidence_code_revision": inputs.evidence_code_revision,
    }
    return SignalForecastIntegrityGateResult(
        decision, reasons_tuple, artifacts, inputs.evaluated_at.isoformat(), inputs.code_revision,
        inputs.evidence_code_revision,
        digest(canonical(payload)),
    )
