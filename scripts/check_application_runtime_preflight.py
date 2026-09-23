"""Offline integrity check for the plan-only canonical runtime manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "deploy/preflight/application_runtime_manifest.json"
SCHEMA_PATH = ROOT / "schemas/application_runtime_preflight.schema.json"
IDENTITY_PATH = ROOT / "config/research_operations_identity.json"
BUCKETS_PATH = ROOT / "config/research_operations_bucket_registry.json"

ALLOWED_APIS = frozenset({
    "compute.googleapis.com", "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com", "logging.googleapis.com",
    "monitoring.googleapis.com", "iam.googleapis.com",
    "serviceusage.googleapis.com",
})


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path.relative_to(ROOT)}") from exc


def validate_manifest(manifest: object, identity: object, bucket_registry: object,
                      schema: object) -> list[str]:
    """Return contract errors; never contacts or changes a cloud resource."""
    errors: list[str] = []
    errors.extend(error.message for error in Draft202012Validator(
        schema, format_checker=FormatChecker()).iter_errors(manifest))
    if errors or not all(isinstance(value, dict) for value in (manifest, identity, bucket_registry)):
        return errors or ["manifest and identity registries must be JSON objects"]

    assert isinstance(manifest, dict)
    assert isinstance(identity, dict)
    assert isinstance(bucket_registry, dict)
    target = manifest["target"]
    runtime = manifest["runtime"]
    authority = manifest["authority"]
    artifact = manifest["artifact"]
    storage = manifest["storage"]
    secrets = manifest["secrets"]
    rollback = manifest["rollback"]
    preflight = manifest["preflight"]

    for key in ("approved_project_id", "approved_project_number"):
        if target.get("project_id" if key.endswith("id") else "project_number") != identity.get(key):
            errors.append(f"target does not match repository-owned {key}")
        if bucket_registry.get(key) != identity.get(key):
            errors.append(f"bucket registry does not match repository-owned {key}")
    if target["project_id"] == target["prior_display_name_project_id"]:
        errors.append("display name is not an approved project ID")
    if target["project_id"] in identity.get("prohibited_project_identifiers", []):
        errors.append("target uses a prohibited project identifier")

    if runtime["service_account"]["proposed_email"] == runtime["deployer_service_account"]["proposed_email"]:
        errors.append("runtime and deployer identities must be separate")
    if authority["deployment_approved"] or authority["deployment_executed"]:
        errors.append("this manifest is plan-only; deployment approval/execution must remain false")
    if authority["api_enablement_performed"] or authority["resource_mutation_performed"] or authority["secrets_read_or_changed"]:
        errors.append("preflight cannot claim cloud mutations or secret access")
    api_names = [item["name"] for item in manifest["apis"]]
    if len(api_names) != len(set(api_names)) or set(api_names) != ALLOWED_APIS:
        errors.append("API list differs from the reviewed allowlist")
    if any(item["state"] != "NOT_ENABLED_BY_THIS_CHANGE" for item in manifest["apis"]):
        errors.append("preflight must not represent API enablement")

    approved = bucket_registry.get("approved_buckets")
    if not isinstance(approved, list):
        errors.append("approved bucket registry must contain a list")
        approved = []
    if any(bucket not in approved for bucket in storage["approved_data_buckets"]):
        errors.append("manifest names a data bucket absent from the approved registry")
    if storage["backup_bucket"] is not None and storage["backup_bucket"] not in approved:
        errors.append("manifest names a backup bucket absent from the approved registry")

    expected_blockers: set[str] = set()
    if target["observation_state"] != "OBSERVED_CURRENT":
        expected_blockers.add("TARGET_RESOURCES_NOT_CURRENTLY_OBSERVED")
    if any(runtime[key]["observation_state"] != "OBSERVED_APPROVED" or runtime[key]["grants_applied"]
           for key in ("service_account", "deployer_service_account")):
        expected_blockers.add("SERVICE_ACCOUNTS_NOT_OBSERVED_OR_APPROVED")
    if artifact["registry_resource"] is None or artifact["approval_state"] != "APPROVED":
        expected_blockers.add("ARTIFACT_REGISTRY_NOT_APPROVED")
    if artifact["image_digest"] is None:
        expected_blockers.add("IMMUTABLE_IMAGE_DIGEST_NOT_PINNED")
    if not storage["approved_data_buckets"] or storage["backup_bucket"] is None:
        expected_blockers.add("NO_APPROVED_DATA_OR_BACKUP_BUCKET")
    if storage["backup_retention_policy"] is None or storage["restore_test_evidence"] is None:
        expected_blockers.add("BACKUP_RETENTION_AND_RESTORE_TEST_MISSING")
    if not secrets["approved_version_references"] or not secrets["secret_names_and_versions_observed"]:
        expected_blockers.add("SECRET_VERSION_REFERENCES_NOT_APPROVED")
    if (rollback["previous_image_digest"] is None or rollback["prechange_data_snapshot"] is None
            or rollback["restore_test_evidence"] is None or rollback["reviewed_owner"] is None):
        expected_blockers.add("ROLLBACK_IMAGE_AND_DATA_SNAPSHOT_MISSING")

    actual_blockers = set(preflight["blockers"])
    if actual_blockers != expected_blockers:
        errors.append("preflight blocker list does not match current evidence gaps")
    if preflight["status"] != ("BLOCKED" if expected_blockers else "READY_FOR_SEPARATE_APPROVAL"):
        errors.append("preflight status does not match current evidence gaps")
    if not expected_blockers and not authority["deployment_approved"]:
        # Readiness is a pre-approval state; this tool never authorizes deployment.
        pass
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH)
    parser.add_argument("--identity", type=Path, default=IDENTITY_PATH)
    parser.add_argument("--buckets", type=Path, default=BUCKETS_PATH)
    args = parser.parse_args()
    try:
        schema = _read(args.schema)
        Draft202012Validator.check_schema(schema)
        errors = validate_manifest(_read(args.manifest), _read(args.identity), _read(args.buckets), schema)
    except (ValueError, KeyError, TypeError) as exc:
        print(f"preflight=INVALID error={exc}")
        return 1
    if errors:
        print("preflight=INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    manifest = _read(args.manifest)
    print(f"preflight={manifest['preflight']['status']} deployment_authorized=false")
    for blocker in manifest["preflight"]["blockers"]:
        print(f"- {blocker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
