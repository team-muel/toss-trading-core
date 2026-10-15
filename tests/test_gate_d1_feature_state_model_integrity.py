from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import shutil
import os
from types import SimpleNamespace

import pytest
from importlib.machinery import EXTENSION_SUFFIXES

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


def test_replaced_public_cache_path_helper_cannot_hide_executable_cache(tmp_path):
    import importlib.util
    import py_compile
    import sys
    source = tmp_path / "source.py"
    stamp = 1_700_000_000_000_000_000
    source.write_bytes(b"value = 9\n")
    os.utime(source, ns=(stamp, stamp))
    py_compile.compile(str(source), doraise=True)
    source.write_bytes(b"value = 1\n")
    os.utime(source, ns=(stamp, stamp))
    program = """
import importlib.util, sys
from pathlib import Path
source=Path(sys.argv[1])
importlib.util.cache_from_source=lambda *args,**kwargs: str(source.parent/'absent.pyc')
from asset_management.validation.feature_state_model_integrity import FeatureStateModelSourceRevisionVerifier
spec=importlib.util.spec_from_file_location('cache_path_probe',source)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if module.value!=9:raise RuntimeError('real executable cache not selected')
if FeatureStateModelSourceRevisionVerifier._python_caches_match(source,source.read_bytes()):
    raise RuntimeError('hidden executable cache accepted')
print('CACHE_PATH_SUBSTITUTION_REJECTED')
"""
    result = subprocess.run([sys.executable, "-c", program, str(source)], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "CACHE_PATH_SUBSTITUTION_REJECTED" in result.stdout


@pytest.mark.parametrize("optimization", (None, "", "1", "2", "3", "5"))
@pytest.mark.parametrize("prefix_kind", ("none", "absolute", "relative"))
@pytest.mark.parametrize("relative", (False, True))
def test_cache_path_derivation_matches_cpython(tmp_path, monkeypatch, optimization, prefix_kind, relative):
    import importlib.util
    import sys
    monkeypatch.chdir(tmp_path)
    prefix = None if prefix_kind == "none" else (
        str(tmp_path / "cache-root") if prefix_kind == "absolute" else "cache-root")
    monkeypatch.setattr(sys, "pycache_prefix", prefix)
    source = Path("nested/source.py") if relative else tmp_path / "nested/source.py"
    expected = Path(importlib.util.cache_from_source(str(source), optimization=optimization))
    assert FeatureStateModelSourceRevisionVerifier._python_cache_path(source, optimization) == expected


def test_public_extension_suffix_list_mutation_cannot_disable_native_check(tmp_path):
    import importlib.machinery
    source = tmp_path / "source.py"
    source.write_bytes(b"value = 1\n")
    suffixes = importlib.machinery.EXTENSION_SUFFIXES
    saved = list(suffixes)
    source.with_name(source.stem + saved[0]).write_bytes(b"untrusted native candidate")
    try:
        suffixes.clear()
        assert not FeatureStateModelSourceRevisionVerifier._python_caches_match(source, source.read_bytes())
    finally:
        suffixes[:] = saved


def test_suffix_snapshot_uses_interpreter_data_when_public_list_was_already_cleared(tmp_path):
    import sys
    source = tmp_path / "source.py"
    source.write_bytes(b"value = 1\n")
    source.with_name(source.stem + EXTENSION_SUFFIXES[0]).write_bytes(b"untrusted native candidate")
    program = """
import importlib.machinery, sys
from pathlib import Path
from _imp import extension_suffixes
importlib.machinery.EXTENSION_SUFFIXES.clear()
from asset_management.validation import feature_state_model_integrity as module
source = Path(sys.argv[1])
if module._SOURCE_EXTENSION_SUFFIXES != tuple(extension_suffixes()):
    raise RuntimeError('snapshot did not capture interpreter data')
if module.FeatureStateModelSourceRevisionVerifier._python_caches_match(source,source.read_bytes()):
    raise RuntimeError('native shadow accepted')
print('EARLY_SUFFIX_SNAPSHOT_VERIFIED')
"""
    result = subprocess.run([sys.executable, "-c", program, str(source)], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "EARLY_SUFFIX_SNAPSHOT_VERIFIED" in result.stdout


@pytest.mark.parametrize("spoof_names", (False, True))
def test_replaced_suffix_getter_is_not_called_or_trusted_at_bootstrap(tmp_path, spoof_names):
    import sys
    source = tmp_path / "source.py"
    source.write_bytes(b"value = 1\n")
    source.with_name(source.stem + EXTENSION_SUFFIXES[0]).write_bytes(b"untrusted native candidate")
    program = """
import _imp, sys
from pathlib import Path
called = []
def replacement():
    called.append(sys._getframe(1).f_globals.get('__name__'))
    return ('.bogus',)
if sys.argv[2] == 'true':
    replacement.__name__ = 'extension_suffixes'
    replacement.__module__ = '_imp'
_imp.extension_suffixes = replacement
from asset_management.validation import feature_state_model_integrity as module
source = Path(sys.argv[1])
if module.__name__ in called:
    raise RuntimeError('capture invoked untrusted getter')
if module._SOURCE_EXTENSION_SUFFIXES != ():
    raise RuntimeError('replaced getter produced authority snapshot')
if module.FeatureStateModelSourceRevisionVerifier._python_caches_match(source,source.read_bytes()):
    raise RuntimeError('native shadow accepted')
print('REPLACED_GETTER_REJECTED')
"""
    result = subprocess.run([sys.executable, "-c", program, str(source),
                             "true" if spoof_names else "false"], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "REPLACED_GETTER_REJECTED" in result.stdout


@pytest.mark.parametrize("invalid", ((), [], ("invalid",), (".pyd", ".pyd")))
def test_invalid_private_extension_suffix_snapshot_fails_closed(tmp_path, monkeypatch, invalid):
    import asset_management.validation.feature_state_model_integrity as module
    source = tmp_path / "source.py"
    source.write_bytes(b"value = 1\n")
    monkeypatch.setattr(module, "_SOURCE_EXTENSION_SUFFIXES", invalid, raising=False)
    assert not FeatureStateModelSourceRevisionVerifier._python_caches_match(source, source.read_bytes())


@pytest.mark.parametrize("suffix", EXTENSION_SUFFIXES)
def test_ignored_native_extension_cannot_shadow_tracked_python(tmp_path, suffix):
    repository = tmp_path / "repository"
    repository.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repository), *args], check=True,
                              capture_output=True, text=True)
    git("init", "--quiet")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    source = repository / "source.py"
    source.write_bytes(b"value = 1\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "source")
    revision, tree = _revision_and_tree(repository)
    verifier = FeatureStateModelSourceRevisionVerifier(repository)
    assert verifier.verify(revision) == (revision, tree)
    native = source.with_name(source.stem + suffix)
    (repository / ".git/info/exclude").write_text(native.name + "\n", encoding="utf-8")
    native.write_bytes(b"untrusted native-module candidate")
    assert not git("status", "--porcelain=v1", "--untracked-files=all").stdout
    assert verifier.verify(revision) is None


@pytest.mark.parametrize("malicious", (False, True))
def test_ignored_timestamp_valid_bytecode_must_match_tracked_source(tmp_path, malicious):
    import importlib.util
    import py_compile
    repository = tmp_path / "repository"
    repository.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repository), *args], check=True,
                              capture_output=True, text=True)
    git("init", "--quiet")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    (repository / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    source = repository / "source.py"
    source.write_bytes(b"value = 1\n")
    stamp = 1_700_000_000_000_000_000
    os.utime(source, ns=(stamp, stamp))
    git("add", ".")
    git("commit", "--quiet", "-m", "source")
    revision, tree = _revision_and_tree(repository)
    if malicious:
        source.write_bytes(b"value = 9\n")
        os.utime(source, ns=(stamp, stamp))
    py_compile.compile(str(source), doraise=True)
    source.write_bytes(b"value = 1\n")
    os.utime(source, ns=(stamp, stamp))
    spec = importlib.util.spec_from_file_location("bytecode_probe", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.value == (9 if malicious else 1)
    assert not git("status", "--porcelain=v1", "--untracked-files=all").stdout
    result = FeatureStateModelSourceRevisionVerifier(repository).verify(revision)
    assert result == (None if malicious else (revision, tree))


@pytest.mark.parametrize("optimization", (0, 1, 2))
def test_python_cache_comparison_checks_each_optimization(tmp_path, optimization):
    import py_compile
    source = tmp_path / "source.py"
    source.write_bytes(b"value = -0.0\n")
    stamp = 1_700_000_000_000_000_000
    os.utime(source, ns=(stamp, stamp))
    py_compile.compile(str(source), doraise=True, optimize=optimization)
    assert FeatureStateModelSourceRevisionVerifier._python_caches_match(source, source.read_bytes())
    source.write_bytes(b"value = +0.0\n")
    os.utime(source, ns=(stamp, stamp))
    assert not FeatureStateModelSourceRevisionVerifier._python_caches_match(source, source.read_bytes())


@pytest.mark.parametrize("level", (3, 5))
@pytest.mark.parametrize("malicious", (False, True))
def test_active_high_optimization_cache_is_verified_in_executing_process(tmp_path, level, malicious):
    import importlib.util
    import py_compile
    import sys
    source = tmp_path / "source.py"
    stamp = 1_700_000_000_000_000_000
    source.write_bytes(b"value = 9\n" if malicious else b"value = 1\n")
    os.utime(source, ns=(stamp, stamp))
    compiled = Path(py_compile.compile(str(source), doraise=True, optimize=2))
    compiled.replace(importlib.util.cache_from_source(str(source), optimization=str(level)))
    source.write_bytes(b"value = 1\n")
    os.utime(source, ns=(stamp, stamp))
    program = """
import importlib.util, sys
from pathlib import Path
from asset_management.validation.feature_state_model_integrity import FeatureStateModelSourceRevisionVerifier
source = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('high_optimization_probe', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if module.value != int(sys.argv[2]):
    raise RuntimeError('active optimization cache was not executed')
verified = FeatureStateModelSourceRevisionVerifier._python_caches_match(source, source.read_bytes())
if verified != (sys.argv[3] == 'true'):
    raise RuntimeError('active optimization cache verification mismatch')
print('ACTIVE_CACHE_VERIFIED')
"""
    result = subprocess.run([sys.executable, "-" + "O" * level, "-c", program, str(source),
                             "9" if malicious else "1", "false" if malicious else "true"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "ACTIVE_CACHE_VERIFIED" in result.stdout


@pytest.mark.parametrize("stale_field", ("mtime", "size"))
def test_timestamp_stale_cache_cannot_execute_and_does_not_block_verified_source(tmp_path, stale_field):
    import importlib.util
    import py_compile
    source = tmp_path / "source.py"
    source.write_bytes(b"value = 9\n")
    stamp = 1_700_000_000_000_000_000
    os.utime(source, ns=(stamp, stamp))
    cache = Path(py_compile.compile(str(source), doraise=True))
    data = bytearray(cache.read_bytes())
    offset = 8 if stale_field == "mtime" else 12
    data[offset:offset + 4] = (int.from_bytes(data[offset:offset + 4], "little") + 1).to_bytes(4, "little")
    cache.write_bytes(data)
    source.write_bytes(b"value = 1\n")
    os.utime(source, ns=(stamp, stamp))
    assert FeatureStateModelSourceRevisionVerifier._python_caches_match(source, source.read_bytes())
    spec = importlib.util.spec_from_file_location("stale_cache_probe", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.value == 1


@pytest.mark.parametrize("invalid", ("magic", "payload", "legacy"))
def test_unsupported_or_malformed_python_cache_fails_closed(tmp_path, invalid):
    import py_compile
    source = tmp_path / "source.py"
    source.write_bytes(b"value = 1\n")
    cache = Path(py_compile.compile(str(source), doraise=True))
    data = cache.read_bytes()
    if invalid == "magic":
        cache.write_bytes(b"BAD!" + data[4:])
    elif invalid == "payload":
        cache.write_bytes(data[:16] + b"invalid marshal payload")
    else:
        source.with_suffix(".pyc").write_bytes(data)
    assert not FeatureStateModelSourceRevisionVerifier._python_caches_match(source, source.read_bytes())


@pytest.mark.parametrize("alteration", ("module_stack", "module_qualname", "nested_stack",
                                        "nested_filename", "nested_qualname", "nested_cellvars"))
def test_cache_code_metadata_matches_fresh_compilation_recursively(tmp_path, alteration):
    import marshal
    import py_compile
    from types import CodeType
    source = tmp_path / "source.py"
    source.write_bytes(b"def f(x):\n return x + 1\n")
    cache = Path(py_compile.compile(str(source), doraise=True))
    data = cache.read_bytes()
    loaded = marshal.loads(data[16:])
    if alteration == "module_stack":
        changed = loaded.replace(co_stacksize=loaded.co_stacksize + 1)
    elif alteration == "module_qualname":
        changed = loaded.replace(co_qualname="unverified")
    else:
        child = next(value for value in loaded.co_consts if type(value) is CodeType)
        if alteration == "nested_stack":
            changed_child = child.replace(co_stacksize=child.co_stacksize + 1)
        elif alteration == "nested_filename":
            changed_child = child.replace(co_filename="unverified.py")
        elif alteration == "nested_cellvars":
            changed_child = child.replace(co_cellvars=("x",))
        else:
            changed_child = child.replace(co_qualname="unverified")
        changed = loaded.replace(co_consts=tuple(changed_child if value is child else value
                                                 for value in loaded.co_consts))
    assert changed == loaded  # Built-in equality omits these compilation fields.
    cache.write_bytes(data[:16] + marshal.dumps(changed))
    assert not FeatureStateModelSourceRevisionVerifier._python_caches_match(source, source.read_bytes())


def test_gate_input_subclass_is_rejected_before_identifier_reads():
    class CallerInput(FeatureStateModelIntegrityGateInput):
        def __getattribute__(self, name):
            if name == "runtime_run_id":
                return "unverified-runtime"
            return super().__getattribute__(name)
    checks = {name: CheckEvidence(True, ("sha256:" + "a" * 64,))
              for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS}
    inputs = CallerInput(datetime(2026, 9, 4, tzinfo=timezone.utc), "runtime@1",
                         "git:" + "a" * 40, "git:" + "a" * 40, checks)
    with pytest.raises(InvariantViolation, match="FEATURE_STATE_MODEL_GATE_INPUT_INVALID"):
        evaluate_feature_state_model_integrity_gate(inputs)


def test_gate_result_uses_entry_snapshot_after_original_input_mutation(monkeypatch):
    import asset_management.validation.feature_state_model_integrity as module
    checks = {name: CheckEvidence(True, ("sha256:" + "a" * 64,))
              for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS}
    inputs = FeatureStateModelIntegrityGateInput(datetime(2026, 9, 4, tzinfo=timezone.utc),
                                                "runtime@1", "git:" + "a" * 40,
                                                "git:" + "a" * 40, checks)
    def mutate_original(store):
        object.__setattr__(inputs, "runtime_run_id", "unverified-runtime")
        object.__setattr__(inputs.checks[REQUIRED_FEATURE_STATE_MODEL_CHECKS[0]], "artifact_ids", ())
        raise InvariantViolation("CANONICAL_D1_STORE_UNVERIFIED")
    monkeypatch.setattr(module, "_trusted_immutable_store", mutate_original)
    result = evaluate_feature_state_model_integrity_gate(inputs)
    assert result.runtime_run_id == "runtime@1"
    assert result.evidence_artifact_ids == ("sha256:" + "a" * 64,)
    assert result.decision is AcceptanceDecision.FAIL


def test_duck_typed_check_equality_cannot_replace_concrete_evidence():
    class CallerCheck:
        passed = True
        artifact_ids = ()
        def __eq__(self, other):
            raise AssertionError("caller equality executed")
    checks = {name: CallerCheck() for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS}
    with pytest.raises(InvariantViolation, match="FEATURE_STATE_MODEL_GATE_CHECK_VALUE_INVALID"):
        FeatureStateModelIntegrityGateInput(datetime(2026, 9, 4, tzinfo=timezone.utc),
                                           "runtime@1", "git:" + "a" * 40,
                                           "git:" + "a" * 40, checks)


@pytest.mark.parametrize("field,value", (("passed", 1), ("artifact_ids", ()),
                                       ("artifact_ids", ["sha256:" + "a" * 64])))
def test_concrete_check_fields_are_revalidated_at_evaluation(field, value):
    checks = {name: CheckEvidence(True, ("sha256:" + "a" * 64,))
              for name in REQUIRED_FEATURE_STATE_MODEL_CHECKS}
    inputs = FeatureStateModelIntegrityGateInput(datetime(2026, 9, 4, tzinfo=timezone.utc),
                                                "runtime@1", "git:" + "a" * 40,
                                                "git:" + "a" * 40, checks)
    object.__setattr__(inputs.checks[REQUIRED_FEATURE_STATE_MODEL_CHECKS[0]], field, value)
    with pytest.raises(InvariantViolation, match="FEATURE_STATE_MODEL_GATE_CHECK_VALUE_INVALID"):
        evaluate_feature_state_model_integrity_gate(inputs)


@pytest.mark.parametrize("name", ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES",
                                  "DYLD_LIBRARY_PATH", "GLIBC_TUNABLES", "LIBPATH", "SHLIB_PATH",
                                  "GCONV_PATH", "ld_preload"))
def test_git_child_environment_excludes_loader_injection(monkeypatch, name):
    monkeypatch.setenv(name, "caller-controlled-loader-input")
    assert name not in FeatureStateModelSourceRevisionVerifier._git_environment()


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
    # Keep the cached file mtime older than the index write, avoiding Git's
    # separate racy-index protection on fast Linux runners.
    old_timestamp = 1_700_000_000_000_000_000
    os.utime(source, ns=(old_timestamp, old_timestamp))
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
