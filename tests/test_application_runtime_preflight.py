from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from asset_management.cli.canonical_d2_production_evidence import _result as canonical_d2_cli_result
from scripts.check_application_runtime_deployment_evidence import validate_evidence
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
    assert EVIDENCE_SCHEMA["$defs"]["resourceObservation"]["properties"]["project_id"]["const"] == IDENTITY["approved_project_id"]


def valid_deployment_evidence():
    observed_at = "2026-09-24T00:00:00Z"
    project_id = IDENTITY["approved_project_id"]
    zone = "asia-northeast3-a"
    disk_id = f"projects/{project_id}/zones/{zone}/disks/runtime-evidence-01"
    snapshot_id = f"projects/{project_id}/global/snapshots/runtime-evidence-20260924"
    runtime_lineage = {
        "runtime_run_id": "runtime-run-20260924-001",
        "as_of_utc": "2026-09-24T00:00:00Z",
        "information_cutoff_utc": "2026-09-23T23:59:00Z",
        "code_revision": "a" * 40,
    }
    d2_replay = canonical_d2_cli_result(
        SimpleNamespace(
            runtime_run_id=runtime_lineage["runtime_run_id"],
            content_hash="1" * 64,
            model_registry_snapshot_id="model-registry-snapshot-001",
            model_registry_binding_hash="2" * 64,
            factor_risk_calculation_id="3" * 64,
            risk_free_manifest_id="4" * 64,
            risk_free_curve_hash="5" * 64,
            accounting_snapshot_id="accounting-snapshot-001",
        ),
        operation_mode="REPLAY_ONLY",
        runtime_lineage=runtime_lineage,
        observed_at=observed_at,
    )
    d2_replay = {
        "cli_result": d2_replay,
        "evidence_reference": "https://evidence.example/d2/replay.json",
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
        "runtime_topology": {
            "instance": {
                "resource_type": "google_compute_instance",
                "resource_id": f"projects/{project_id}/zones/{zone}/instances/asset-runtime-01",
                "project_id": project_id,
                "observed_at": observed_at,
                "source_reference": "https://evidence.example/observations/instance.json",
                "attached_evidence_disk": {
                    "resource_type": "google_compute_disk",
                    "resource_id": disk_id,
                    "project_id": project_id,
                    "observed_at": observed_at,
                    "source_reference": "https://evidence.example/observations/disk.json",
                    "mount_path": "/var/lib/asset-management/runtime",
                    "backup_snapshot": {
                        "resource_type": "google_compute_snapshot",
                        "resource_id": snapshot_id,
                        "project_id": project_id,
                        "source_disk_resource_id": disk_id,
                        "observed_at": observed_at,
                        "source_reference": "https://evidence.example/observations/snapshot.json",
                        "retention_policy": {
                            "policy_reference": "https://evidence.example/retention/runtime-policy.json",
                            "minimum_retention_days": 30,
                            "immutable_until": "2026-10-24T00:00:00Z",
                        },
                        "restore_test": {
                            "evidence_sha256": "sha256:" + "f" * 64,
                            "evidence_reference": "https://evidence.example/restore-tests/runtime.json",
                            "verified_at": observed_at,
                            "restored_snapshot_resource_id": snapshot_id,
                            "restore_succeeded": True,
                            "database_integrity_check": "ok",
                        },
                    },
                },
                "attached_service_account": {
                    "resource_type": "google_iam_service_account",
                    "resource_id": f"projects/{project_id}/serviceAccounts/asset-runtime@{project_id}.iam.gserviceaccount.com",
                    "project_id": project_id,
                    "observed_at": observed_at,
                    "source_reference": "https://evidence.example/observations/service-account.json",
                },
            }
        },
        "runtime_lineage": {
            **runtime_lineage,
            "evidence_reference": "https://evidence.example/runtime/lineage.json",
        },
        "d2_replay": d2_replay,
        "iam_policy_export_hash": "sha256:" + "d" * 64,
        "api_state_hash": "sha256:" + "e" * 64,
        "runtime_mode": "READ_ONLY",
        "live_trading_enabled": False,
        "migration_versions": [1],
        "observability_config_hash": "sha256:" + "1" * 64,
        "scheduler_inventory": {
            "artifact_sha256": "sha256:" + "2" * 64,
            "source_reference": "https://evidence.example/inventory/scheduler.json",
            "project_id": project_id,
            "observed_at": observed_at,
            "inspected_surfaces": ["cloud_scheduler_jobs", "vm_systemd_timers"],
            "active_scheduled_job_count": 0,
            "active_vm_timer_count": 0,
        },
        "ingress_inventory": {
            "artifact_sha256": "sha256:" + "3" * 64,
            "source_reference": "https://evidence.example/inventory/ingress.json",
            "project_id": project_id,
            "observed_at": observed_at,
            "inspected_surfaces": ["project_firewall_rules", "vm_network_interfaces", "vm_listening_sockets"],
            "inbound_firewall_rule_count": 0,
            "external_ip_count": 0,
            "listening_socket_count": 0,
        },
        "observed_at": observed_at,
        "actor": "operator@example.com",
        "approval_reference": "https://evidence.example/approvals/deployment.json",
    }


def test_future_deployment_evidence_is_bound_to_approved_project_and_topology():
    validator = Draft202012Validator(EVIDENCE_SCHEMA, format_checker=FormatChecker())
    evidence = valid_deployment_evidence()
    validator.validate(evidence)
    assert validate_evidence(evidence, EVIDENCE_SCHEMA, IDENTITY) == []

    evidence["project"]["project_number"] = "1"
    assert list(validator.iter_errors(evidence))

    evidence = valid_deployment_evidence()
    evidence["runtime_topology"]["instance"]["attached_evidence_disk"]["project_id"] = "unapproved-project"
    assert list(validator.iter_errors(evidence))

    evidence = valid_deployment_evidence()
    evidence["runtime_topology"]["instance"]["resource_id"] = "runtime-vm"
    assert list(validator.iter_errors(evidence))

    evidence = valid_deployment_evidence()
    evidence["runtime_topology"]["instance"]["attached_service_account"]["resource_id"] = "runtime-service-account"
    assert list(validator.iter_errors(evidence))

    assert validate_evidence([], EVIDENCE_SCHEMA, IDENTITY)


def test_future_deployment_evidence_requires_absence_and_durable_restore_proof():
    validator = Draft202012Validator(EVIDENCE_SCHEMA, format_checker=FormatChecker())
    evidence = valid_deployment_evidence()
    evidence["scheduler_inventory"]["active_scheduled_job_count"] = 1
    evidence["ingress_inventory"]["listening_socket_count"] = 1
    evidence["scheduler_inventory"]["project_id"] = "unapproved-project"
    evidence["ingress_inventory"]["inspected_surfaces"].remove("vm_network_interfaces")
    assert list(validator.iter_errors(evidence))

    evidence = valid_deployment_evidence()
    restore = evidence["runtime_topology"]["instance"]["attached_evidence_disk"]["backup_snapshot"]["restore_test"]
    restore["restore_succeeded"] = False
    assert list(validator.iter_errors(evidence))

    evidence = valid_deployment_evidence()
    snapshot = evidence["runtime_topology"]["instance"]["attached_evidence_disk"]["backup_snapshot"]
    snapshot["project_id"] = "unapproved-project"
    assert list(validator.iter_errors(evidence))

    evidence = valid_deployment_evidence()
    snapshot = evidence["runtime_topology"]["instance"]["attached_evidence_disk"]["backup_snapshot"]
    snapshot["source_disk_resource_id"] = f"projects/{IDENTITY['approved_project_id']}/zones/asia-northeast3-a/disks/unrelated-disk"
    assert validate_evidence(evidence, EVIDENCE_SCHEMA, IDENTITY)

    evidence = valid_deployment_evidence()
    evidence["runtime_topology"]["instance"]["attached_evidence_disk"]["backup_snapshot"]["restore_test"]["restored_snapshot_resource_id"] = "projects/toss-trading-core-lab-508411/global/snapshots/unrelated-snapshot"
    assert validate_evidence(evidence, EVIDENCE_SCHEMA, IDENTITY)

    evidence = valid_deployment_evidence()
    evidence["runtime_lineage"]["information_cutoff_utc"] = "2026-09-24T00:01:00Z"
    evidence["d2_replay"]["cli_result"]["runtime_run_id"] = "different-runtime-run"
    evidence["runtime_lineage"]["code_revision"] = "b" * 40
    assert validate_evidence(evidence, EVIDENCE_SCHEMA, IDENTITY)

    evidence = valid_deployment_evidence()
    evidence["d2_replay"]["cli_result"]["operation_mode"] = "RECORD"
    assert validate_evidence(evidence, EVIDENCE_SCHEMA, IDENTITY)

    evidence = valid_deployment_evidence()
    evidence["d2_replay"]["cli_result"]["operation_receipt_sha256"] = "0" * 64
    assert validate_evidence(evidence, EVIDENCE_SCHEMA, IDENTITY)

    evidence = valid_deployment_evidence()
    evidence["runtime_topology"] = "unknown"
    assert validate_evidence(evidence, EVIDENCE_SCHEMA, IDENTITY)


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
