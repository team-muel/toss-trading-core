from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.check_application_runtime_preflight import validate_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "deploy/preflight/application_runtime_manifest.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "schemas/application_runtime_preflight.schema.json").read_text(encoding="utf-8"))
IDENTITY = json.loads((ROOT / "config/research_operations_identity.json").read_text(encoding="utf-8"))
BUCKETS = json.loads((ROOT / "config/research_operations_bucket_registry.json").read_text(encoding="utf-8"))
EVIDENCE_SCHEMA = json.loads((ROOT / "schemas/application_runtime_deployment_evidence.schema.json").read_text(encoding="utf-8"))


def errors(manifest=MANIFEST, identity=IDENTITY, buckets=BUCKETS):
    return validate_manifest(manifest, identity, buckets, SCHEMA)


def test_plan_manifest_and_future_evidence_schema_are_valid_contracts():
    Draft202012Validator.check_schema(SCHEMA)
    Draft202012Validator.check_schema(EVIDENCE_SCHEMA)
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(MANIFEST)
    assert errors() == []
    assert MANIFEST["preflight"]["status"] == "BLOCKED"
    assert MANIFEST["authority"]["deployment_executed"] is False
    assert EVIDENCE_SCHEMA["$defs"]["observedProject"]["properties"]["project_id"]["const"] == IDENTITY["approved_project_id"]
    assert EVIDENCE_SCHEMA["$defs"]["observedProject"]["properties"]["project_number"]["const"] == IDENTITY["approved_project_number"]
    assert EVIDENCE_SCHEMA["$defs"]["observedResource"]["properties"]["project_id"]["const"] == IDENTITY["approved_project_id"]


def valid_deployment_evidence():
    observed_at = "2026-09-24T00:00:00Z"
    project_id = IDENTITY["approved_project_id"]

    def resource(resource_type, resource_id):
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "project_id": project_id,
            "observed_at": observed_at,
            "source_reference": "https://evidence.example/observations/resource.json",
        }

    return {
        "schema_version": "canonical-application-runtime-deployment-evidence@1",
        "source_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "manifest_hash": "sha256:" + "c" * 64,
        "project": {
            "resource_type": "google_project",
            "resource_id": project_id,
            "project_id": project_id,
            "project_number": IDENTITY["approved_project_number"],
            "observed_at": observed_at,
            "source_reference": "https://evidence.example/observations/project.json",
        },
        "resources": [
            resource("google_compute_instance", "runtime-vm"),
            resource("google_compute_disk", "runtime-disk"),
            resource("google_iam_service_account", "runtime-service-account"),
        ],
        "iam_policy_export_hash": "sha256:" + "d" * 64,
        "api_state_hash": "sha256:" + "e" * 64,
        "runtime_mode": "READ_ONLY",
        "live_trading_enabled": False,
        "migration_versions": [1],
        "evidence_store_identity": resource(
            "google_compute_disk",
            f"projects/{project_id}/zones/asia-northeast3-a/disks/runtime-disk",
        ),
        "backup_restore": {
            "source_disk": resource(
                "google_compute_disk",
                f"projects/{project_id}/zones/asia-northeast3-a/disks/runtime-disk",
            ),
            "backup_snapshot": resource(
                "google_compute_snapshot",
                f"projects/{project_id}/global/snapshots/runtime-snapshot",
            ),
            "snapshot_id": f"projects/{project_id}/global/snapshots/runtime-snapshot",
            "retention_policy": {
                "policy_reference": "https://evidence.example/retention/runtime-policy.json",
                "minimum_retention_days": 30,
                "immutable_until": "2026-10-24T00:00:00Z",
            },
            "restore_test_evidence_hash": "sha256:" + "f" * 64,
            "restore_test_reference": "https://evidence.example/restore-tests/runtime.json",
            "verified_at": observed_at,
        },
        "observability_config_hash": "sha256:" + "1" * 64,
        "scheduler_inventory": {
            "artifact_sha256": "sha256:" + "2" * 64,
            "source_reference": "https://evidence.example/inventory/scheduler.json",
            "observed_at": observed_at,
            "absence_verified": True,
        },
        "ingress_inventory": {
            "artifact_sha256": "sha256:" + "3" * 64,
            "source_reference": "https://evidence.example/inventory/ingress.json",
            "observed_at": observed_at,
            "absence_verified": True,
        },
        "observed_at": observed_at,
        "actor": "operator@example.com",
        "approval_reference": "https://evidence.example/approvals/deployment.json",
    }


def test_future_deployment_evidence_is_bound_to_approved_project_and_topology():
    validator = Draft202012Validator(EVIDENCE_SCHEMA, format_checker=FormatChecker())
    evidence = valid_deployment_evidence()
    validator.validate(evidence)

    evidence["project"]["project_number"] = "1"
    assert list(validator.iter_errors(evidence))

    evidence = valid_deployment_evidence()
    evidence["resources"][0]["project_id"] = "unapproved-project"
    assert list(validator.iter_errors(evidence))

    evidence = valid_deployment_evidence()
    evidence["resources"] = [evidence["resources"][0]]
    assert list(validator.iter_errors(evidence))


def test_future_deployment_evidence_requires_absence_and_durable_restore_proof():
    validator = Draft202012Validator(EVIDENCE_SCHEMA, format_checker=FormatChecker())
    evidence = valid_deployment_evidence()
    evidence["scheduler_inventory"]["absence_verified"] = False
    evidence["ingress_inventory"]["absence_verified"] = False
    evidence["backup_restore"]["source_disk"]["resource_type"] = "local_file"
    evidence["backup_restore"].pop("retention_policy")
    assert list(validator.iter_errors(evidence))

    evidence = valid_deployment_evidence()
    evidence["backup_restore"]["backup_snapshot"]["project_id"] = "unapproved-project"
    assert list(validator.iter_errors(evidence))


@pytest.mark.parametrize("mutation", [
    lambda m: m["target"].update(project_id="toss-trading-core-lab"),
    lambda m: m["authority"].update(deployment_executed=True),
    lambda m: m["runtime"].update(scheduler_enabled=True),
    lambda m: m["runtime"].update(inbound_network_listener=True),
    lambda m: m["runtime"].update(live_trading_enabled=True),
    lambda m: m["runtime"]["deployer_service_account"].update(
        proposed_email=m["runtime"]["service_account"]["proposed_email"]),
    lambda m: m["apis"].append({"name": "cloudscheduler.googleapis.com", "purpose": "timer", "state": "NOT_ENABLED_BY_THIS_CHANGE"}),
    lambda m: m["storage"].update(approved_data_buckets=["gs://unregistered-bucket"]),
    lambda m: m["preflight"].update(blockers=[]),
    lambda m: m["artifact"].update(image_digest="sha256:" + "a" * 64),
])
def test_preflight_rejects_authority_or_unverified_identity_drift(mutation):
    candidate = deepcopy(MANIFEST)
    mutation(candidate)
    assert errors(candidate)


def test_repository_identity_and_bucket_registry_must_agree():
    other_identity = deepcopy(IDENTITY)
    other_identity["approved_project_number"] = "1"
    assert any("approved_project_number" in error for error in errors(identity=other_identity))

    other_buckets = deepcopy(BUCKETS)
    other_buckets["approved_project_id"] = "another-project"
    assert any("bucket registry" in error for error in errors(buckets=other_buckets))


def test_unknown_or_extra_api_is_not_implicitly_allowed():
    candidate = deepcopy(MANIFEST)
    candidate["apis"][0]["state"] = "ENABLED"
    assert errors(candidate)

    candidate = deepcopy(MANIFEST)
    candidate["apis"].append({"name": "cloudscheduler.googleapis.com", "purpose": "new scheduler", "state": "NOT_ENABLED_BY_THIS_CHANGE"})
    assert errors(candidate)
