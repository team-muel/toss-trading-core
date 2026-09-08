"""Fail CI on credential patterns in tracked working-tree files without printing values."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys


RULES = (
    ("PRIVATE_KEY", rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    ("AWS_ACCESS_KEY", rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ("GITHUB_TOKEN", rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ("GOOGLE_API_KEY", rb"\bAIza[A-Za-z0-9_-]{35}\b"),
    ("SLACK_TOKEN", rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    ("OPENAI_KEY", rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b"),
    ("CREDENTIAL_LITERAL", rb"(?i)(?<![A-Za-z0-9_])[\"']?((?:[A-Za-z0-9]+_)*(?:client_secret|api_key|access_token|refresh_token|password))[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_+/=.-]{20,})[\"']"),
)
COMPILED_RULES = tuple((name, re.compile(pattern)) for name, pattern in RULES)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


def scan_bytes(path: str, content: bytes) -> tuple[Finding, ...]:
    findings = []
    for rule, pattern in COMPILED_RULES:
        for match in pattern.finditer(content):
            if (rule == "CREDENTIAL_LITERAL" and match.group(1).isupper()
                    and match.group(2) == match.group(1) + b"_SECRET"):
                # Exact environment-name -> Secret Manager environment-name mapping.
                continue
            findings.append(Finding(path, content.count(b"\n", 0, match.start()) + 1, rule))
    return tuple(sorted(set(findings), key=lambda item: (item.path, item.line, item.rule)))


def scan_repository(root: Path) -> tuple[Finding, ...]:
    root = root.resolve()
    result = subprocess.run(
        ["git", "ls-files", "--cached", "-z"], cwd=root, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError("SECRET_SCAN_GIT_FAILED")
    findings = []
    for raw in sorted(set(result.stdout.split(b"\0")) - {b""}):
        relative = raw.decode("utf-8", errors="strict")
        path = root / relative
        # No symlinks, external files, ignored files or runtime credential directories.
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise RuntimeError("SECRET_SCAN_UNSAFE_TRACKED_PATH")
        findings.extend(scan_bytes(relative, path.read_bytes()))
    return tuple(findings)


def main(root: Path | None = None) -> int:
    try:
        findings = scan_repository(root or Path(__file__).resolve().parents[1])
    except (OSError, UnicodeError, RuntimeError):
        print("SECRET_SCAN_FAILED: tracked files could not be fully inspected", file=sys.stderr)
        return 2
    for finding in findings:
        # Never emit the source line, matched value, subprocess stderr or file content.
        print(f"{finding.path}:{finding.line}: {finding.rule}", file=sys.stderr)
    if findings:
        print(f"SECRET_SCAN_BLOCKED: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("secret pattern scan passed (tracked working-tree files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
