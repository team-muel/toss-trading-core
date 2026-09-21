from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    REQUIRED_FEATURE_STATE_MODEL_CHECKS, AcceptanceDecision, CheckEvidence,
    FeatureStateModelIntegrityGateInput, FeatureStateModelSourceRevisionVerifier,
    evaluate_feature_state_model_integrity_gate,
)


ROOT = Path(__file__).parents[1]


def _revision_and_tree(repository: Path = ROOT) -> tuple[str, str]:
    revision = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"], check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    return f"git:{revision}", tree


def _check_evidence(store: ImmutableDatasetStore, *, code_revision: str,
                    source_tree: str) -> dict[str, CheckEvidence]:
    checks = {}
    for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS:
        identifier = store.catalog("feature-state-model-gate-evidence", {
            "schema_version": "feature-state-model-gate-evidence@1",
            "check_name": name,
            "code_revision": code_revision,
            "source_tree": source_tree,
        })
        checks[name] = CheckEvidence(True, (f"sha256:{identifier}",))
    return checks


def passing_input(tmp_path: Path):
    code_revision, source_tree = _revision_and_tree()
    store = ImmutableDatasetStore(tmp_path)
    checks = _check_evidence(store, code_revision=code_revision, source_tree=source_tree)
    return (
        FeatureStateModelIntegrityGateInput(
            datetime(2026, 9, 6, tzinfo=timezone.utc), code_revision, code_revision, checks),
        store,
        FeatureStateModelSourceRevisionVerifier(ROOT),
    )


def test_current_head_immutable_evidence_can_pass_d1(tmp_path):
    inputs, store, verifier = passing_input(tmp_path)
    result = evaluate_feature_state_model_integrity_gate(
        inputs, evidence_store=store, source_revision_verifier=verifier)
    assert result.decision is AcceptanceDecision.PASS
    assert result.reason_codes == ()
    assert len(result.evidence_artifact_ids) == len(REQUIRED_FEATURE_STATE_MODEL_CHECKS)


@pytest.mark.parametrize("failed_check", REQUIRED_FEATURE_STATE_MODEL_CHECKS)
def test_every_feature_state_model_check_fails_closed(failed_check, tmp_path):
    inputs, store, verifier = passing_input(tmp_path)
    checks = dict(inputs.checks)
    checks[failed_check] = CheckEvidence(False, checks[failed_check].artifact_ids)
    result = evaluate_feature_state_model_integrity_gate(
        replace(inputs, checks=checks), evidence_store=store, source_revision_verifier=verifier)
    assert result.decision is AcceptanceDecision.FAIL
    assert result.reason_codes == (f"CHECK_FAILED:{failed_check}",)


def test_missing_unknown_or_empty_evidence_is_rejected(tmp_path):
    inputs, _, _ = passing_input(tmp_path)
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        FeatureStateModelIntegrityGateInput(datetime.now(timezone.utc), "revision", "revision", {})
    with pytest.raises(InvariantViolation, match="TIME_NOT_AWARE"):
        FeatureStateModelIntegrityGateInput(
            datetime.now(), inputs.code_revision, inputs.evidence_code_revision, inputs.checks)
    with pytest.raises(InvariantViolation, match="CHECK_UNKNOWN"):
        CheckEvidence(None, ("run",))
    with pytest.raises(InvariantViolation, match="EVIDENCE_INVALID"):
        CheckEvidence(True, ())


def test_missing_or_wrong_immutable_store_fails_closed(tmp_path):
    inputs, _, verifier = passing_input(tmp_path / "published")
    absent = evaluate_feature_state_model_integrity_gate(inputs, source_revision_verifier=verifier)
    wrong_store = evaluate_feature_state_model_integrity_gate(
        inputs, evidence_store=ImmutableDatasetStore(tmp_path / "other"), source_revision_verifier=verifier)
    assert absent.decision is AcceptanceDecision.FAIL
    assert "EVIDENCE_STORE_UNVERIFIED" in absent.reason_codes
    assert wrong_store.decision is AcceptanceDecision.FAIL
    assert set(wrong_store.reason_codes) == {
        f"EVIDENCE_ARTIFACT_UNVERIFIED:{name}" for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS
    }


def test_mismatched_or_nonexistent_source_revision_cannot_pass_d1(tmp_path):
    inputs, store, verifier = passing_input(tmp_path)
    mismatch = evaluate_feature_state_model_integrity_gate(
        replace(inputs, evidence_code_revision="git:" + "e" * 40),
        evidence_store=store, source_revision_verifier=verifier)
    nonexistent = "git:" + "f" * 40
    forged_checks = _check_evidence(store, code_revision=nonexistent, source_tree="e" * 40)
    unknown = evaluate_feature_state_model_integrity_gate(
        FeatureStateModelIntegrityGateInput(
            datetime(2026, 9, 6, tzinfo=timezone.utc), nonexistent, nonexistent, forged_checks),
        evidence_store=store, source_revision_verifier=verifier)
    assert "EVIDENCE_CODE_REVISION_MISMATCH" in mismatch.reason_codes
    assert "SOURCE_REVISION_UNVERIFIED" in unknown.reason_codes
    assert not mismatch.decision is AcceptanceDecision.PASS
    assert not unknown.decision is AcceptanceDecision.PASS


def test_real_non_head_commit_cannot_permit_m4(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    for command in (
            ["git", "init", "--quiet", str(repository)],
            ["git", "-C", str(repository), "config", "user.name", "test"],
            ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
    ):
        subprocess.run(command, check=True, capture_output=True, text=True)
    tracked_file = repository / "tracked.txt"
    tracked_file.write_text("parent\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "commit", "--quiet", "-m", "parent"],
                   check=True, capture_output=True, text=True)
    historical, source_tree = _revision_and_tree(repository)
    tracked_file.write_text("child\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "commit", "--quiet", "-m", "child"],
                   check=True, capture_output=True, text=True)
    store = ImmutableDatasetStore(tmp_path / "store")
    checks = _check_evidence(store, code_revision=historical, source_tree=source_tree)
    result = evaluate_feature_state_model_integrity_gate(
        FeatureStateModelIntegrityGateInput(
            datetime(2026, 9, 6, tzinfo=timezone.utc), historical, historical, checks),
        evidence_store=store, source_revision_verifier=FeatureStateModelSourceRevisionVerifier(repository))
    assert result.decision is AcceptanceDecision.FAIL
    assert "SOURCE_REVISION_UNVERIFIED" in result.reason_codes


def test_historical_record_is_not_current_acceptance_authority(tmp_path):
    recorded = json.loads((ROOT / "docs/evidence/gate_d1_feature_state_model_integrity_2026-09-06.json").read_text())
    schema = json.loads((ROOT / "schemas/feature_state_model_integrity_acceptance.schema.json").read_text())
    assert recorded["decision"] == "PASS"
    assert recorded["code_revision"] == "a80176c"
    assert all(item.startswith("pytest:") for item in recorded["evidence_artifact_ids"])
    assert "evidence_code_revision" in schema["required"]
    inputs, store, verifier = passing_input(tmp_path)
    assert set(schema["required"]) == set(asdict(
        evaluate_feature_state_model_integrity_gate(
            inputs, evidence_store=store, source_revision_verifier=verifier)))
