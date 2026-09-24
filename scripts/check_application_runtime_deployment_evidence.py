"""Offline structure and relationship checks for future deployment evidence.

This checker validates supplied evidence only. It performs no cloud reads and
never authorizes, prepares, or executes a deployment.
"""

import argparse
from datetime import datetime
from hashlib import sha256
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
    if errors:
        return sorted(set(errors))
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
    replay = evidence.get("d2_replay", {}).get("cli_result", {})
    if lineage.get("code_revision") != evidence.get("source_sha"):
        errors.append("runtime lineage code_revision must match the deployment source_sha")
    if replay.get("runtime_run_id") != lineage.get("runtime_run_id"):
        errors.append("D2 replay runtime_run_id must match the observed runtime lineage")
    replay_lineage = replay.get("runtime_lineage", {})
    for field in ("runtime_run_id", "as_of_utc", "information_cutoff_utc", "code_revision"):
        if replay_lineage.get(field) != lineage.get(field):
            errors.append(f"D2 replay runtime lineage {field} must match the deployment lineage")
    if replay.get("operation_mode") != "REPLAY_ONLY":
        errors.append("D2 evidence must come from a replay-only CLI operation")
    receipt = replay.get("operation_receipt_sha256")
    if isinstance(receipt, str):
        receipt_body = {key: value for key, value in replay.items() if key != "operation_receipt_sha256"}
        canonical_receipt = json.dumps(receipt_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if sha256(canonical_receipt.encode("utf-8")).hexdigest() != receipt:
            errors.append("D2 operation receipt hash does not match the replay-only CLI result")
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
