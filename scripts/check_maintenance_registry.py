"""Offline maintenance routing, not semantic approval or a live-trading gate."""
from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {f"AMA-{number}" for number in range(135, 148)}


def valid_path(value: object, *, pattern: bool = False) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if "\\" in value or ":" in value or any(part in {"", ".", ".."} for part in value.split("/")):
        return False
    # A literal prefix prevents wildcard-only spellings such as *** or ?*
    # from making every otherwise-unmapped repository path appear owned.
    return not pattern or (value[0] not in "*?[" and "{" not in value and "}" not in value)


def strings(value: object) -> bool:
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, str) and item.strip() for item in value)
            and len(value) == len(set(value)))


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate(registry: object) -> None:
    if not isinstance(registry, dict) or type(registry.get("version")) is not int or registry["version"] != 1:
        raise ValueError("registry version must be integer 1")
    if not isinstance(registry.get("baseline_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}", registry["baseline_sha"]):
        raise ValueError("baseline_sha must be a full commit SHA")
    if registry.get("linear_register") != "https://linear.app/muelsyse/document/maintenance-surface-register-toss-trading-core-fd92fa5c5bb2":
        raise ValueError("unexpected Linear source of truth")
    surfaces = registry.get("surfaces")
    if not isinstance(surfaces, list) or len(surfaces) != len(EXPECTED):
        raise ValueError("exactly 13 maintenance umbrellas are required")
    seen = set()
    for surface in surfaces:
        if not isinstance(surface, dict):
            raise ValueError("surface must be an object")
        issue = surface.get("issue")
        if not isinstance(issue, str) or issue not in EXPECTED or issue in seen:
            raise ValueError("missing, duplicate, or unknown umbrella")
        seen.add(issue)
        if not isinstance(surface.get("title"), str) or not surface["title"].strip():
            raise ValueError(f"{issue}: title required")
        if not strings(surface.get("paths")) or not all(valid_path(p, pattern=True) for p in surface["paths"]):
            raise ValueError(f"{issue}: invalid path patterns")
        if not strings(surface.get("evidence")):
            raise ValueError(f"{issue}: evidence requirements required")
    rules = registry.get("cross_surface_rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("cross-surface rules required")
    for rule in rules:
        if not isinstance(rule, dict) or not strings(rule.get("paths")) or not all(valid_path(p, pattern=True) for p in rule["paths"]):
            raise ValueError("invalid cross-surface path patterns")
        if not strings(rule.get("issues")) or len(rule["issues"]) < 2 or not set(rule["issues"]) <= EXPECTED:
            raise ValueError("cross-surface rule must reference known umbrellas")
        if not isinstance(rule.get("reason"), str) or not rule["reason"].strip():
            raise ValueError("cross-surface reason required")


def classify(registry: dict, paths: list[str]) -> dict:
    validate(registry)
    routing = {}
    for path in sorted(set(paths)):
        if not valid_path(path):
            raise ValueError(f"invalid repository-relative path: {path!r}")
        issues = {s["issue"] for s in registry["surfaces"] if any(fnmatchcase(path, p) for p in s["paths"])}
        for rule in registry["cross_surface_rules"]:
            if any(fnmatchcase(path, p) for p in rule["paths"]):
                issues.update(rule["issues"])
        routing[path] = sorted(issues)
    umbrellas = sorted({issue for issues in routing.values() for issue in issues})
    return {"umbrellas": umbrellas, "cross_surface": len(umbrellas) > 1,
            "paths": routing, "unmapped": [p for p, issues in routing.items() if not issues]}


def null_paths(data: bytes) -> list[str]:
    if not data or not data.endswith(b"\0"):
        raise ValueError("path input must be nonempty and NUL-terminated")
    paths = data[:-1].decode("utf-8").split("\0")
    if not all(valid_path(path) for path in paths):
        raise ValueError("path input contains an empty or invalid path")
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--registry", type=Path, default=ROOT / "config/maintenance_surfaces.json")
    parser.add_argument("--paths0-from", type=Path, help="NUL-terminated paths from git diff --no-renames --name-only -z")
    args = parser.parse_args(argv)
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        validate(registry)
        paths = list(args.paths)
        if args.paths0_from is not None:
            paths.extend(null_paths(args.paths0_from.read_bytes()))
        result = classify(registry, paths)
        result["mode"] = "path-classification" if paths else "registry-validation-only"
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result["unmapped"] else 0
    except (OSError, ValueError, UnicodeError) as error:
        print(f"maintenance registry error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
