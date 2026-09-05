import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/check_secrets.py"
spec = importlib.util.spec_from_file_location("check_secrets", SCRIPT)
scanner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scanner
spec.loader.exec_module(scanner)


@pytest.mark.parametrize("rule,token", [
    ("PRIVATE_KEY", "-----BEGIN " + "PRIVATE KEY-----"),
    ("AWS_ACCESS_KEY", "AKIA" + "A" * 16),
    ("GITHUB_TOKEN", "ghp_" + "a" * 36),
    ("GOOGLE_API_KEY", "AIza" + "b" * 35),
    ("SLACK_TOKEN", "xoxb-" + "1" * 24),
    ("OPENAI_KEY", "sk-proj-" + "a" * 40),
    ("CREDENTIAL_LITERAL", 'client_secret = "' + "aB7_" * 8 + '"'),
])
def test_detects_credentials_without_retaining_values(rule, token):
    findings = scanner.scan_bytes("sample.txt", ("safe\n" + token).encode())
    assert scanner.Finding("sample.txt", 2, rule) in findings
    assert token not in repr(findings)


def test_references_and_empty_values_are_not_credentials():
    assert scanner.scan_bytes("config.py", b'api_key = os.environ["API_KEY"]\npassword = ""') == ()


def test_exact_secret_manager_environment_mapping_is_not_a_value_allowlist():
    key = "TOSS_CLIENT_SECRET"
    reference = f'"{key}": "{key}_SECRET"'.encode()
    assert scanner.scan_bytes("mapping.py", reference) == ()
    literal = f'"{key}": "OTHER_{"X" * 30}_SECRET"'.encode()
    assert scanner.scan_bytes("mapping.py", literal)[0].rule == "CREDENTIAL_LITERAL"


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_tracked_files_scanned_including_binary_but_untracked_ignored(tmp_path, capsys):
    git(tmp_path, "init")
    (tmp_path / "safe.txt").write_text("safe")
    token = "ghp_" + "z" * 36
    (tmp_path / ".env").write_text(token)
    git(tmp_path, "add", "safe.txt")
    assert scanner.main(tmp_path) == 0
    (tmp_path / "binary.dat").write_bytes(b"\x00" + token.encode())
    git(tmp_path, "add", "binary.dat")
    assert scanner.main(tmp_path) == 1
    output = capsys.readouterr()
    assert "binary.dat:1: GITHUB_TOKEN" in output.err
    assert token not in output.err + output.out


def test_deleted_tracked_file_and_non_repository_fail_closed(tmp_path, capsys):
    assert scanner.main(tmp_path) == 2
    git(tmp_path, "init")
    path = tmp_path / "missing.txt"
    path.write_text("safe")
    git(tmp_path, "add", "missing.txt")
    path.unlink()
    assert scanner.main(tmp_path) == 2
    assert "SECRET_SCAN_FAILED" in capsys.readouterr().err


def test_ci_runs_secret_scan_before_dependency_installation():
    workflow = (SCRIPT.parents[1] / ".github/workflows/ci.yml").read_text()
    assert workflow.index("python scripts/check_secrets.py") < workflow.index("pip install")
