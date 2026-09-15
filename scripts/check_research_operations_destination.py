"""Validate research artifact destinations against the repository-owned registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = Path("config/research_operations_identity.json")
REGISTRY_PATH = Path("config/research_operations_bucket_registry.json")
_BUCKET_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,220}[a-z0-9])?$")
_DESTINATION_PATHS = (
    "cloudbuild.yaml",
    "cloudbuild.research.yaml",
    "scripts/provision_research_automation_gcp.sh",
    "scripts/run_research_automation_gcp.sh",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("destination registry must be an object")
    return value


def load_identity(path: Path) -> dict[str, Any]:
    value = load_json(path)
    project_id = value.get("approved_project_id")
    if value.get("schema_version") != "research-operations-identity@1" or not isinstance(
        project_id, str
    ) or not project_id or not isinstance(value.get("approved_project_number"), str) or not value[
        "approved_project_number"
    ].isdecimal():
        raise ValueError("research operations identity is invalid")
    return value


def load_registry(path: Path, identity: dict[str, Any]) -> dict[str, Any]:
    value = load_json(path)
    if value.get("schema_version") != "research-operations-bucket-registry@1":
        raise ValueError("research operations bucket registry schema is invalid")
    if value.get("approved_project_id") != identity["approved_project_id"]:
        raise ValueError("bucket registry project does not match operations identity")
    if value.get("approved_project_number") != identity["approved_project_number"]:
        raise ValueError("bucket registry project number does not match operations identity")
    buckets = value.get("approved_buckets")
    if not isinstance(buckets, list):
        raise ValueError("approved bucket registry is invalid")
    names: set[str] = set()
    for item in buckets:
        if not isinstance(item, dict):
            raise ValueError("approved bucket record is invalid")
        name = item.get("bucket_name")
        project_id = item.get("project_id")
        approval_id = item.get("approval_evidence_id")
        if (
            not isinstance(name, str)
            or not _BUCKET_NAME.fullmatch(name)
            or project_id != identity["approved_project_id"]
            or not isinstance(approval_id, str)
            or not approval_id.strip()
            or name in names
        ):
            raise ValueError("approved bucket record is invalid")
        names.add(name)
    return value


def parse_gs_uri(uri: str) -> str:
    if not isinstance(uri, str) or not uri:
        raise ValueError("research destination URI is missing")
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "gs"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not _BUCKET_NAME.fullmatch(parsed.netloc)
    ):
        raise ValueError("research destination URI is invalid")
    parts = parsed.path.split("/")
    if any(part in {".", ".."} for part in parts) or "//" in parsed.path:
        raise ValueError("research destination URI path is invalid")
    return parsed.netloc


def require_approved_bucket(bucket_name: str, registry: dict[str, Any]) -> None:
    if not isinstance(bucket_name, str) or not _BUCKET_NAME.fullmatch(bucket_name):
        raise ValueError("research artifact bucket is invalid")
    approved = {item["bucket_name"] for item in registry["approved_buckets"]}
    if not approved:
        raise ValueError("no research artifact bucket is approved")
    if bucket_name not in approved:
        raise ValueError("research artifact bucket is not approved")


def require_bucket_project(bucket_name: str, registry: dict[str, Any]) -> None:
    try:
        result = subprocess.run(
            [
                "gcloud",
                "storage",
                "buckets",
                "describe",
                f"gs://{bucket_name}",
                "--format=value(projectNumber)",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("approved research artifact bucket cannot be verified") from error
    if result.stdout.strip() != registry["approved_project_number"]:
        raise ValueError("approved research artifact bucket belongs to another project")


def validate_repository(root: Path) -> None:
    for relative in _DESTINATION_PATHS:
        source = (root / relative).read_text(encoding="utf-8")
        if "check_research_operations_destination.py" not in source:
            raise ValueError(f"destination path omits registry guard: {relative}")
    for relative in ("cloudbuild.yaml", "cloudbuild.research.yaml"):
        source = (root / relative).read_text(encoding="utf-8")
        if "RESEARCH_ARTIFACT_BUCKET=${_RESEARCH_ARTIFACT_BUCKET}" not in source or (
            "--bucket=\"$${RESEARCH_ARTIFACT_BUCKET}\" --verify-bucket-project"
            not in source
        ):
            raise ValueError(f"Cloud Build artifact destination is not registry guarded: {relative}")
    provisioner = (root / "scripts/provision_research_automation_gcp.sh").read_text(
        encoding="utf-8"
    )
    if "--bucket=\"${BUCKET_NAME}\" --verify-bucket-project" not in provisioner:
        raise ValueError("provisioner bucket destination is not registry guarded")
    if "--bucket=\"${BUILD_SOURCE_BUCKET}\" --verify-bucket-project" not in provisioner:
        raise ValueError("provisioner build-source destination is not registry guarded")
    if "--uri=\"${GCS_URI}\" --verify-bucket-project" not in (
        root / "scripts/run_research_automation_gcp.sh"
    ).read_text(encoding="utf-8"):
        raise ValueError("runtime bucket destination is not registry guarded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--bucket")
    target.add_argument("--uri")
    parser.add_argument("--verify-bucket-project", action="store_true")
    parser.add_argument("--check-repository", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--identity", type=Path, default=IDENTITY_PATH)
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    identity_path = args.identity if args.identity.is_absolute() else root / args.identity
    registry_path = args.registry if args.registry.is_absolute() else root / args.registry
    try:
        identity = load_identity(identity_path)
        registry = load_registry(registry_path, identity)
        bucket_name = args.bucket if args.bucket is not None else None
        if args.uri is not None:
            bucket_name = parse_gs_uri(args.uri)
        if bucket_name is not None:
            require_approved_bucket(bucket_name, registry)
            if args.verify_bucket_project:
                require_bucket_project(bucket_name, registry)
        elif args.verify_bucket_project:
            raise ValueError("--verify-bucket-project requires --bucket or --uri")
        if args.check_repository:
            validate_repository(root)
        if args.bucket is None and args.uri is None and not args.check_repository:
            raise ValueError("--bucket, --uri, or --check-repository is required")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"research operations destination check failed: {error}", file=sys.stderr)
        return 1
    print("research_operations_destination=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
