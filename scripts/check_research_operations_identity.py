"""Fail closed when legacy research operations target an unapproved GCP project."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = Path("config/research_operations_identity.json")
_OPERATIONAL_PATHS = (
    "cloudbuild.yaml",
    "cloudbuild.research.yaml",
    "deploy/systemd/toss-research-automation@.service",
    "deploy/systemd/toss-stock-recommendations.service",
    "scripts/check_research_identity_gcp.sh",
    "scripts/load_gcp_secrets.sh",
    "scripts/provision_research_automation_gcp.sh",
    "scripts/run_research_automation_gcp.sh",
    "scripts/run_stock_recommendations_gcp.sh",
    "scripts/run_toss_history_collection_gcp.sh",
)
_SHELL_CALLERS = _OPERATIONAL_PATHS[4:]


def load_identity(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "research-operations-identity@1":
        raise ValueError("research operations identity schema is invalid")
    project_id = value.get("approved_project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("approved research project ID is missing")
    project_number = value.get("approved_project_number")
    if not isinstance(project_number, str) or not project_number.isdecimal():
        raise ValueError("approved research project number is invalid")
    prohibited = value.get("prohibited_project_identifiers")
    if not isinstance(prohibited, list) or not all(
        isinstance(item, str) and item.strip() for item in prohibited
    ):
        raise ValueError("prohibited research project identifiers are invalid")
    if project_id in prohibited:
        raise ValueError("approved research project ID is prohibited")
    return value


def require_approved_project(project_id: str, identity: dict[str, Any]) -> None:
    approved = identity["approved_project_id"]
    if project_id != approved:
        raise ValueError(
            "research operations project is not approved; "
            f"expected={approved} actual={project_id or 'missing'}"
        )


def contains_project_identifier(source: str, identifier: str) -> bool:
    return re.search(
        rf"(?<![A-Za-z0-9-]){re.escape(identifier)}(?![A-Za-z0-9-])", source
    ) is not None


def validate_repository(root: Path, identity: dict[str, Any]) -> None:
    approved = identity["approved_project_id"]
    prohibited = tuple(identity["prohibited_project_identifiers"])
    for relative in _OPERATIONAL_PATHS:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"required operations path is missing: {relative}")
        source = path.read_text(encoding="utf-8")
        if any(contains_project_identifier(source, identifier) for identifier in prohibited):
            raise ValueError(f"prohibited project identifier in operations path: {relative}")
        if relative != "scripts/load_gcp_secrets.sh" and approved not in source:
            raise ValueError(f"approved project identity missing from operations path: {relative}")
    for relative in _SHELL_CALLERS:
        source = (root / relative).read_text(encoding="utf-8")
        guard = source.find("check_research_operations_identity.py")
        if guard < 0:
            raise ValueError(f"shell operation omits project identity guard: {relative}")
        first_gcloud = source.find("gcloud ")
        if first_gcloud >= 0 and guard > first_gcloud:
            raise ValueError(f"shell operation reaches gcloud before identity guard: {relative}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id")
    parser.add_argument("--check-repository", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--identity", type=Path, default=IDENTITY_PATH)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    identity_path = args.identity if args.identity.is_absolute() else root / args.identity
    try:
        identity = load_identity(identity_path)
        if args.project_id is not None:
            require_approved_project(args.project_id, identity)
        if args.check_repository:
            validate_repository(root, identity)
        if args.project_id is None and not args.check_repository:
            raise ValueError("--project-id or --check-repository is required")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"research operations identity check failed: {error}", file=sys.stderr)
        return 1
    print(
        "research_operations_identity=ok "
        f"project_id={identity['approved_project_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
