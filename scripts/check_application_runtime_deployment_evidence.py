"""Offline structure and relationship checks for future deployment evidence.

This checker validates supplied evidence only. It performs no cloud reads and
never authorizes, prepares, or executes a deployment.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]


def validate_evidence(evidence, schema, identity):
    if not isinstance(evidence, dict):
        return ["deployment evidence must be a JSON object"]
    if not isinstance(schema, dict) or not isinstance(identity, dict):
        return ["evidence schema and repository identity must be JSON objects"]
    errors = [
        error.message
        for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(evidence)
    ]
    approved_project_id = identity.get("approved_project_id")
    approved_project_number = identity.get("approved_project_number")
    project_schema = schema.get("$defs", {}).get("observedProject", {}).get("properties", {})
    if project_schema.get("project_id", {}).get("const") != approved_project_id:
        errors.append("evidence schema project_id is out of sync with the approved repository identity")
    if project_schema.get("project_number", {}).get("const") != approved_project_number:
        errors.append("evidence schema project_number is out of sync with the approved repository identity")

    topology = evidence.get("runtime_topology", {})
    instance = topology.get("instance", {})
    disk = instance.get("attached_evidence_disk", {})
    snapshot = disk.get("backup_snapshot", {})
    restore_test = snapshot.get("restore_test", {})
    if snapshot.get("source_disk_resource_id") != disk.get("resource_id"):
        errors.append("backup snapshot source disk must match the attached evidence disk")
    if restore_test.get("restored_snapshot_resource_id") != snapshot.get("resource_id"):
        errors.append("restore test snapshot must match the attached evidence disk snapshot")

    lineage = evidence.get("runtime_lineage", {})
    replay = evidence.get("d2_replay", {})
    if lineage.get("code_revision") != evidence.get("source_sha"):
        errors.append("runtime lineage code_revision must match the deployment source_sha")
    if replay.get("runtime_run_id") != lineage.get("runtime_run_id"):
        errors.append("D2 replay runtime_run_id must match the observed runtime lineage")
    try:
        as_of = datetime.fromisoformat(str(lineage.get("as_of_utc", "")).replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(str(lineage.get("information_cutoff_utc", "")).replace("Z", "+00:00"))
        if cutoff > as_of:
            errors.append("runtime information_cutoff_utc must not be later than as_of_utc")
    except ValueError:
        # Schema validation reports malformed or timezone-free timestamps.
        pass

    for field in ("scheduler_inventory", "ingress_inventory"):
        if evidence.get(field, {}).get("project_id") != approved_project_id:
            errors.append(f"{field} project_id must match the approved repository identity")
    return sorted(set(errors))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="future deployment evidence JSON to validate")
    parser.add_argument("--schema", type=Path, default=ROOT / "schemas/application_runtime_deployment_evidence.schema.json")
    parser.add_argument("--identity", type=Path, default=ROOT / "config/research_operations_identity.json")
    args = parser.parse_args(argv)

    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
        identity = json.loads(args.identity.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "evidence_contract_valid": False,
            "deployment_authorized": False,
            "errors": [f"evidence inputs are unreadable or invalid JSON: {exc}"],
        }, sort_keys=True))
        return 2
    errors = validate_evidence(evidence, schema, identity)
    print(json.dumps({
        "evidence_contract_valid": not errors,
        "deployment_authorized": False,
        "errors": errors,
    }, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
