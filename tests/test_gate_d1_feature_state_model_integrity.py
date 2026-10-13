from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import shutil
import os
from types import SimpleNamespace

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import InvariantViolation
from asset_management.validation import (
    REQUIRED_FEATURE_STATE_MODEL_CHECKS, AcceptanceDecision, CheckEvidence,
    FeatureStateModelIntegrityGateInput, FeatureStateModelSourceRevisionVerifier,
    evaluate_feature_state_model_integrity_gate,
)
from asset_management.validation.feature_state_model_integrity import (
    _executing_source_identity, SourceGitExecutablePin,
)
from asset_management.data.immutable import digest


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("change", ("unstaged", "staged", "untracked"))
def test_dirty_checkout_cannot_verify_committed_source(tmp_path, change):
    repository = tmp_path / "repository"
    repository.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repository), *args], check=True,
                              capture_output=True, text=True)
    git("init", "--quiet")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    source = repository / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    git("add", "source.py")
    git("commit", "--quiet", "-m", "source")
    revision, tree = _revision_and_tree(repository)
    verifier = FeatureStateModelSourceRevisionVerifier(repository)
    assert verifier.verify(revision) == (revision, tree)
    if change == "untracked":
        (repository / "injected.py").write_text("value = 2\n", encoding="utf-8")
    else:
        source.write_text("value = 2\n", encoding="utf-8")
        if change == "staged":
            git("add", "source.py")
    assert verifier.verify(revision) is None


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


@pytest.mark.parametrize("override", ("repository_env", "inline_config", "global_config", "local_config"))
def test_git_overrides_cannot_hide_dirty_executing_worktree(tmp_path, monkeypatch, override):
    repository = tmp_path / "executing"
    clone = tmp_path / "clean"
    repository.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repository), *args], check=True,
                              capture_output=True, text=True)
    git("init", "--quiet")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    source = repository / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    git("add", "source.py")
    git("commit", "--quiet", "-m", "source")
    revision, _ = _revision_and_tree(repository)
    subprocess.run(["git", "clone", "--quiet", str(repository), str(clone)],
                   check=True, capture_output=True, text=True)
    verifier = FeatureStateModelSourceRevisionVerifier(repository)
    assert verifier.verify(revision) is not None
    source.write_text("value = 2\n", encoding="utf-8")
    assert verifier.verify(revision) is None
    if override == "repository_env":
        monkeypatch.setenv("GIT_DIR", str(clone / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(clone))
    elif override == "inline_config":
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(clone))
    elif override == "global_config":
        config = tmp_path / "override.gitconfig"
        config.write_text(f"[core]\n worktree = {clone.as_posix()}\n", encoding="utf-8")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    else:
        git("config", "core.worktree", str(clone))
    assert verifier.verify(revision) is None


@pytest.mark.parametrize("flag", ("--assume-unchanged", "--skip-worktree"))
@pytest.mark.parametrize("modified", (False, True))
def test_index_flags_cannot_hide_source_changes(tmp_path, flag, modified):
    repository = tmp_path / "repository"
    repository.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repository), *args], check=True,
                              capture_output=True, text=True)
    git("init", "--quiet")
    source = repository / "source.py"
    source.write_bytes(b"value = 1\n")
    git("add", "source.py")
    git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "source")
    revision, _ = _revision_and_tree(repository)
    verifier = FeatureStateModelSourceRevisionVerifier(repository)
    assert verifier.verify(revision) is not None
    git("update-index", flag, "source.py")
    if modified:
        source.write_bytes(b"value = 2\n")
    assert verifier.verify(revision) is None


def test_fsmonitor_hook_cannot_hide_modified_source(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repository), *args], check=True,
                              capture_output=True, text=True)
    git("init", "--quiet")
    source = repository / "source.py"
    source.write_bytes(b"value = 1\n")
    git("add", "source.py")
    git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "source")
    revision, _ = _revision_and_tree(repository)
    hook = tmp_path / "fsmonitor.sh"
    hook.write_bytes(b"#!/bin/sh\nprintf 'test-token\\0'\n")
    hook.chmod(0o755)
    git("config", "core.fsmonitor", hook.as_posix())
    git("config", "core.fsmonitorHookVersion", "2")
    assert git("status", "--porcelain=v1").stdout == ""
    source.write_bytes(b"value = 2\n")
    assert FeatureStateModelSourceRevisionVerifier(repository).verify(revision) is None


def test_restored_mtime_and_minimal_stat_cannot_hide_modified_bytes(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repository), *args], check=True,
                              capture_output=True, text=True)
    git("init", "--quiet")
    source = repository / "source.py"
    source.write_bytes(b"value = 1\n")
    git("add", "source.py")
    git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "source")
    git("config", "core.trustctime", "false")
    git("config", "core.checkStat", "minimal")
    revision, _ = _revision_and_tree(repository)
    git("status", "--porcelain=v1")
    original = source.stat()
    source.write_bytes(b"value = 2\n")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert git("status", "--porcelain=v1").stdout == ""
    assert FeatureStateModelSourceRevisionVerifier(repository).verify(revision) is None


@pytest.mark.parametrize("case", ("crlf", "clean_filter"))
def test_tracked_byte_comparison_preserves_text_and_rejects_filtered_changes(tmp_path, case):
    repository = tmp_path / "repository"
    repository.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repository), *args], check=True,
                              capture_output=True, text=True)
    git("init", "--quiet")
    source = repository / "source.py"
    attributes = b"source.py text eol=lf\n" if case == "crlf" else b"source.py filter=masked\n"
    (repository / ".gitattributes").write_bytes(attributes)
    source.write_bytes(b"value = 1\r\n" if case == "crlf" else b"value = 1\n")
    git("add", ".gitattributes", "source.py")
    git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "source")
    revision, _ = _revision_and_tree(repository)
    verifier = FeatureStateModelSourceRevisionVerifier(repository)
    assert verifier.verify(revision) is not None
    if case == "clean_filter":
        git("config", "filter.masked.clean", "printf 'value = 1\\n'")
        source.write_bytes(b"value = 2\n")
        assert git("status", "--porcelain=v1").stdout == ""
        assert verifier.verify(revision) is None


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
            datetime(2026, 9, 6, tzinfo=timezone.utc), "fixture-runtime", code_revision,
            code_revision, checks),
        store,
        FeatureStateModelSourceRevisionVerifier(ROOT),
    )


@pytest.mark.parametrize("override", ("subclass", "other_checkout", "git_executable"))
def test_caller_cannot_select_source_verification_authority(tmp_path, override):
    inputs, store, _ = passing_input(tmp_path / "store")
    _, tree = _revision_and_tree()
    if override == "subclass":
        class CallerSource(FeatureStateModelSourceRevisionVerifier):
            def verify(self, code_revision):
                return code_revision, tree
        verifier = CallerSource(ROOT)
    elif override == "other_checkout":
        clone = tmp_path / "clone"
        subprocess.run(["git", "clone", "--quiet", "--shared", str(ROOT), str(clone)],
                       check=True, capture_output=True, text=True)
        verifier = FeatureStateModelSourceRevisionVerifier(clone)
        assert verifier.verify(inputs.code_revision) == (inputs.code_revision, tree)
    else:
        verifier = FeatureStateModelSourceRevisionVerifier(ROOT, git_executable=shutil.which("git"))
    result = evaluate_feature_state_model_integrity_gate(
        inputs, evidence_store=store, source_revision_verifier=verifier)
    assert result.decision is AcceptanceDecision.FAIL
    assert "SOURCE_REVISION_UNVERIFIED" in result.reason_codes


def test_source_identity_is_resolved_internally_from_executing_checkout():
    revision, tree = _revision_and_tree()
    executable = Path(shutil.which("git")).resolve()
    pin = SourceGitExecutablePin(str(executable), digest(executable.read_bytes()))
    assert _executing_source_identity(revision, None, git_pin=pin) == (revision, tree)


def test_missing_git_pin_cannot_establish_executing_source_identity():
    revision, _ = _revision_and_tree()
    assert _executing_source_identity(revision, None) is None


def test_git_pin_mismatch_is_rejected_before_execution(monkeypatch):
    revision, _ = _revision_and_tree()
    pin = SourceGitExecutablePin(str(Path(shutil.which("git")).resolve()), "f" * 64)
    def forbidden(*args, **kwargs):
        raise AssertionError("unverified executable ran")
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert _executing_source_identity(revision, None, git_pin=pin) is None


def test_path_selected_git_cannot_forge_executing_source(monkeypatch):
    revision, _ = _revision_and_tree()
    monkeypatch.setattr(shutil, "which", lambda executable: "/caller/git")
    def forged_git(args, **kwargs):
        if "rev-parse" in args:
            output = "f" * 40 if "tree" in args[-1] else revision.removeprefix("git:")
            return SimpleNamespace(returncode=0, stdout=output + "\n")
        return SimpleNamespace(returncode=0, stdout="" if kwargs.get("text") else b"")
    monkeypatch.setattr(subprocess, "run", forged_git)
    assert _executing_source_identity(revision, None) is None


def test_fixture_catalog_labels_cannot_pass_d1_without_a_replayed_runtime_bundle(tmp_path):
    inputs, store, verifier = passing_input(tmp_path)
    result = evaluate_feature_state_model_integrity_gate(
        inputs, evidence_store=store, source_revision_verifier=verifier)
    assert result.decision is AcceptanceDecision.FAIL
    assert "RUNTIME_EVIDENCE_REPOSITORY_UNVERIFIED" in result.reason_codes


@pytest.mark.parametrize("failed_check", REQUIRED_FEATURE_STATE_MODEL_CHECKS)
def test_every_feature_state_model_check_fails_closed(failed_check, tmp_path):
    inputs, store, verifier = passing_input(tmp_path)
    checks = dict(inputs.checks)
    checks[failed_check] = CheckEvidence(False, checks[failed_check].artifact_ids)
    result = evaluate_feature_state_model_integrity_gate(
        replace(inputs, checks=checks), evidence_store=store, source_revision_verifier=verifier)
    assert result.decision is AcceptanceDecision.FAIL
    assert f"CHECK_FAILED:{failed_check}" in result.reason_codes


def test_missing_unknown_or_empty_evidence_is_rejected(tmp_path):
    inputs, _, _ = passing_input(tmp_path)
    with pytest.raises(InvariantViolation, match="CHECK_SET_INVALID"):
        FeatureStateModelIntegrityGateInput(datetime.now(timezone.utc), "runtime", "revision", "revision", {})
    with pytest.raises(InvariantViolation, match="TIME_NOT_AWARE"):
        FeatureStateModelIntegrityGateInput(
            datetime.now(), inputs.runtime_run_id, inputs.code_revision,
            inputs.evidence_code_revision, inputs.checks)
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
    assert "RUNTIME_EVIDENCE_REPOSITORY_UNVERIFIED" in wrong_store.reason_codes
    assert {f"EVIDENCE_ARTIFACT_UNVERIFIED:{name}" for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS} <= set(wrong_store.reason_codes)


def test_mismatched_or_nonexistent_source_revision_cannot_pass_d1(tmp_path):
    inputs, store, verifier = passing_input(tmp_path)
    mismatch = evaluate_feature_state_model_integrity_gate(
        replace(inputs, evidence_code_revision="git:" + "e" * 40),
        evidence_store=store, source_revision_verifier=verifier)
    nonexistent = "git:" + "f" * 40
    forged_checks = _check_evidence(store, code_revision=nonexistent, source_tree="e" * 40)
    unknown = evaluate_feature_state_model_integrity_gate(
        FeatureStateModelIntegrityGateInput(
            datetime(2026, 9, 6, tzinfo=timezone.utc), "runtime", nonexistent,
            nonexistent, forged_checks),
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
            datetime(2026, 9, 6, tzinfo=timezone.utc), "runtime", historical, historical, checks),
        evidence_store=store, source_revision_verifier=FeatureStateModelSourceRevisionVerifier(repository))
    assert result.decision is AcceptanceDecision.FAIL
    assert "SOURCE_REVISION_UNVERIFIED" in result.reason_codes


def test_historical_record_is_not_current_acceptance_authority(tmp_path):
    recorded = json.loads((ROOT / "docs/evidence/gate_d1_feature_state_model_integrity_2026-09-06.json").read_text())
    schema = json.loads((ROOT / "schemas/feature_state_model_integrity_acceptance.schema.json").read_text())
    assert recorded["decision"] == "PASS"
    assert recorded["code_revision"] == "a80176c"
    assert all(item.startswith("pytest:") for item in recorded["evidence_artifact_ids"])
    assert {"runtime_run_id", "evidence_code_revision"} <= set(schema["required"])
    inputs, store, verifier = passing_input(tmp_path)
    assert set(schema["required"]) == set(asdict(
        evaluate_feature_state_model_integrity_gate(
            inputs, evidence_store=store, source_revision_verifier=verifier)))
