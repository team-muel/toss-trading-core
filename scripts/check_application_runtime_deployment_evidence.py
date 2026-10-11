"""Verify future deployment evidence against the canonical persisted D2 bundle.

The checker reads local evidence stores and performs only the canonical D2
repository's read-only replay. It does not create evidence or authorize a
deployment.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from jsonschema import Draft202012Validator, FormatChecker

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.data.raw_store import SQLiteRawResponseStore
from asset_management.config.migrations import load_migration_catalog
from asset_management.domain.errors import AssetManagementError
from asset_management.time.clock import SystemClock
from asset_management.validation import CanonicalD2ProductionEvidenceRepository
from asset_management.validation.external_attestation import (
    require_runtime_attestor_registry, require_external_evidence_attestation,
)


ROOT = Path(__file__).resolve().parents[1]
MAXIMUM_STATE_AGE = timedelta(hours=1)


def _instant(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return parsed.astimezone(timezone.utc)


def verify_operations_observations(connection, runtime_run_id, observations, *, clock=None):
    """Verify existing externally attested exports; never collect or persist them."""
    now = (clock or SystemClock()).now_utc()
    identity = json.loads((ROOT / "config/research_operations_identity.json").read_text(encoding="utf-8"))
    project = identity["approved_project_id"]
    endpoints = {
        "iam_policy_observation": ("https://cloudasset.googleapis.com/v1/projects/347216292745:searchAllIamPolicies", "GET", "results"),
        "api_state_observation": (f"https://serviceusage.googleapis.com/v1/projects/{identity['approved_project_number']}/services?filter=state%3AENABLED", "GET", "services"),
    }
    runtime = connection.execute(
        "SELECT information_cutoff_utc FROM am_runtime_run WHERE runtime_run_id=?",
        (runtime_run_id,),
    ).fetchone()
    if runtime is None:
        raise ValueError("CANONICAL_D2_RUNTIME_MISSING")
    registry = require_runtime_attestor_registry(conn=connection, runtime_run_id=runtime_run_id,
                                                cutoff=_instant(runtime[0]))
    verified = {}
    for name, (endpoint, method, collection) in endpoints.items():
        observation = observations[name]
        response = SQLiteRawResponseStore(connection).verified(observation["raw_response_id"])
        if (response.source != "gcp-operations" or response.endpoint != endpoint or
                response.http_method != method or response.status_code != 200 or
                response.schema_version != "gcp-operation-export@1" or
                observation["project_id"] != project or observation["source_reference"] != endpoint or
                observation["artifact_sha256"] != "sha256:" + response.response_hash or
                _instant(observation["observed_at"]) != response.received_at or
                response.requested_at > response.received_at or
                response.requested_at > now or response.received_at > now or
                now - response.requested_at > MAXIMUM_STATE_AGE or
                not isinstance(response.body, dict) or
                not isinstance(response.body.get(collection), list) or response.body.get("nextPageToken")):
            raise ValueError("GCP_OPERATION_OBSERVATION_UNVERIFIED")
        require_external_evidence_attestation(conn=connection, raw_response_id=observation["raw_response_id"],
                                             response=response, cutoff=now, registry=registry)
        verified[name] = dict(observation)
    return verified


def verify_absence_inventories(connection, runtime_run_id, inventories, instance_id, *, clock=None):
    """Read existing signed collector exports and derive absence from their contents."""
    now = (clock or SystemClock()).now_utc()
    row = connection.execute("SELECT information_cutoff_utc FROM am_runtime_run WHERE runtime_run_id=?",
                             (runtime_run_id,)).fetchone()
    if row is None:
        raise ValueError("CANONICAL_D2_RUNTIME_MISSING")
    registry = require_runtime_attestor_registry(conn=connection, runtime_run_id=runtime_run_id,
                                                cutoff=_instant(row[0]))
    surfaces = {
        "scheduler_inventory": {"cloud_scheduler_jobs": "active_scheduled_job_count", "vm_systemd_timers": "active_vm_timer_count"},
        "ingress_inventory": {"project_firewall_rules": "inbound_firewall_rule_count", "vm_network_interfaces": "external_ip_count", "vm_listening_sockets": "listening_socket_count"},
    }
    verified = {}
    for name, mapping in surfaces.items():
        claim = inventories[name]
        response = SQLiteRawResponseStore(connection).verified(claim["raw_response_id"])
        endpoint = f"inventory://{instance_id}/{name}"
        body = response.body
        if (response.source != "canonical-operations-inventory" or response.endpoint != endpoint or
                response.http_method != "GET" or response.status_code != 200 or
                response.schema_version != "canonical-absence-inventory@1" or
                claim["source_reference"] != endpoint or
                claim["artifact_sha256"] != "sha256:" + response.response_hash or
                _instant(claim["observed_at"]) != response.received_at or
                response.requested_at > response.received_at or response.received_at > now or
                now - response.requested_at > MAXIMUM_STATE_AGE or
                not isinstance(body, dict) or body.get("project_id") != claim["project_id"] or
                body.get("runtime_run_id") != runtime_run_id or body.get("instance_resource_id") != instance_id or
                body.get("coverage") != "COMPLETE_PROJECT_AND_INSTANCE" or
                not isinstance(body.get("surfaces"), dict) or set(body["surfaces"]) != set(mapping) or
                set(claim["inspected_surfaces"]) != set(mapping)):
            raise ValueError("ABSENCE_INVENTORY_UNVERIFIED")
        for surface, count in mapping.items():
            entries = body["surfaces"][surface]
            if not isinstance(entries, list) or entries or claim[count] != len(entries):
                raise ValueError("ABSENCE_INVENTORY_CONTAINS_RESOURCES")
        require_external_evidence_attestation(conn=connection, raw_response_id=claim["raw_response_id"],
                                             response=response, cutoff=now, registry=registry)
        verified[name] = dict(claim)
    return verified


def verify_recovery_observation(connection, runtime_run_id, observation, instance, *, clock=None):
    """Verify a pre-existing signed snapshot/export and restore report."""
    now = (clock or SystemClock()).now_utc()
    row = connection.execute("SELECT information_cutoff_utc FROM am_runtime_run WHERE runtime_run_id=?",
                             (runtime_run_id,)).fetchone()
    if row is None:
        raise ValueError("CANONICAL_D2_RUNTIME_MISSING")
    registry = require_runtime_attestor_registry(conn=connection, runtime_run_id=runtime_run_id,
                                                cutoff=_instant(row[0]))
    response = SQLiteRawResponseStore(connection).verified(observation["raw_response_id"])
    disk = instance["attached_evidence_disk"]
    snapshot = disk["backup_snapshot"]
    restore = snapshot["restore_test"]
    report = {key: value for key, value in restore.items() if key not in ("evidence_sha256", "evidence_reference")}
    endpoint = f"recovery://{disk['resource_id']}"
    expected = {"project_id": observation["project_id"], "runtime_run_id": runtime_run_id,
                "instance_resource_id": instance["resource_id"], "source_disk_resource_id": disk["resource_id"],
                "snapshot": {key: value for key, value in snapshot.items() if key != "restore_test"},
                "restore_report": report, "restore_reference": restore["evidence_reference"]}
    if (response.source != "canonical-operations-recovery" or response.endpoint != endpoint or
            response.http_method != "GET" or response.status_code != 200 or
            response.schema_version != "canonical-recovery-export@1" or
            observation["source_reference"] != endpoint or
            observation["artifact_sha256"] != "sha256:" + response.response_hash or
            _instant(observation["observed_at"]) != response.received_at or
            response.requested_at > response.received_at or response.received_at > now or
            now - response.requested_at > MAXIMUM_STATE_AGE or response.body != expected or
            snapshot["source_disk_resource_id"] != disk["resource_id"] or
            restore["restored_snapshot_resource_id"] != snapshot["resource_id"] or
            restore["restore_succeeded"] is not True or restore["database_integrity_check"] != "ok" or
            restore["evidence_sha256"] != "sha256:" + digest(canonical(report))):
        raise ValueError("RECOVERY_OBSERVATION_UNVERIFIED")
    require_external_evidence_attestation(conn=connection, raw_response_id=observation["raw_response_id"],
                                         response=response, cutoff=now, registry=registry)
    return {"recovery_observation": dict(observation)}


def replay_persisted_d2(database, immutable_store, runtime_run_id, *, observations=None, inventories=None,
                        instance_id=None, recovery_observation=None, instance=None):
    """Replay, without writes, one existing canonical D2 bundle and return its source lineage."""
    database = Path(database)
    if not database.is_file():
        raise ValueError("CANONICAL_D2_EVIDENCE_DATABASE_MISSING")
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        catalog = load_migration_catalog(ROOT / "schemas")
        expected = tuple((item.version, item.name, item.content_hash) for item in catalog)
        applied = tuple(connection.execute(
            "SELECT version, name, content_hash FROM schema_migration ORDER BY version"
        ))
        if applied != expected:
            raise ValueError("CANONICAL_D2_EVIDENCE_SCHEMA_MISSING")
        repository = CanonicalD2ProductionEvidenceRepository(connection, SystemClock())
        result = repository.replay(
            runtime_run_id=runtime_run_id,
            store=ImmutableDatasetStore(Path(immutable_store)),
        )
        row = connection.execute(
            "SELECT runtime_run_id, as_of_utc, information_cutoff_utc, code_revision "
            "FROM am_runtime_run WHERE runtime_run_id=?",
            (runtime_run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("CANONICAL_D2_RUNTIME_MISSING")
        lineage = dict(zip(
            ("runtime_run_id", "as_of_utc", "information_cutoff_utc", "code_revision"),
            map(str, row),
        ))
        verified_operations = (verify_operations_observations(connection, runtime_run_id, observations)
                               if observations is not None else {})
        if inventories is not None:
            verified_operations.update(verify_absence_inventories(connection, runtime_run_id, inventories, instance_id))
        if recovery_observation is not None:
            verified_operations.update(verify_recovery_observation(connection, runtime_run_id, recovery_observation, instance))
        return result, lineage, tuple(row[0] for row in applied), verified_operations
    finally:
        connection.close()


def validate_evidence(evidence, schema, identity, *, persisted_d2=None, persisted_lineage=None,
                      persisted_migrations=None, persisted_operations=None, clock=None):
    if not isinstance(evidence, dict):
        return ["deployment evidence must be a JSON object"]
    if not isinstance(schema, dict) or not isinstance(identity, dict):
        return ["evidence schema and repository identity must be JSON objects"]
    trusted_schema = json.loads((ROOT / "schemas/application_runtime_deployment_evidence.schema.json").read_text(encoding="utf-8"))
    trusted_identity = json.loads((ROOT / "config/research_operations_identity.json").read_text(encoding="utf-8"))
    if schema != trusted_schema or identity != trusted_identity:
        return ["evidence contract and identity must match the repository-controlled definitions"]
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

    topology = evidence["runtime_topology"]
    instance = topology["instance"]
    disk = instance["attached_evidence_disk"]
    snapshot = disk["backup_snapshot"]
    restore_test = snapshot["restore_test"]
    if snapshot["source_disk_resource_id"] != disk["resource_id"]:
        errors.append("backup snapshot source disk must match the attached evidence disk")
    if restore_test["restored_snapshot_resource_id"] != snapshot["resource_id"]:
        errors.append("restore test snapshot must match the attached evidence disk snapshot")

    lineage = evidence["runtime_lineage"]
    replay = evidence["d2_replay"]
    if lineage["code_revision"] != evidence["source_sha"]:
        errors.append("runtime lineage code_revision must match the deployment source_sha")
    if replay["runtime_run_id"] != lineage["runtime_run_id"]:
        errors.append("D2 replay runtime_run_id must match the observed runtime lineage")
    for field in ("scheduler_inventory", "ingress_inventory"):
        if evidence[field]["project_id"] != approved_project_id:
            errors.append(f"{field} project_id must match the approved repository identity")

    if persisted_d2 is None or persisted_lineage is None:
        errors.append("canonical D2 replay against the persisted evidence store is required")
    else:
        result_fields = {
            "runtime_run_id": persisted_d2.runtime_run_id,
            "canonical_evidence_hash": persisted_d2.content_hash,
            "model_registry_snapshot_id": persisted_d2.model_registry_snapshot_id,
            "model_registry_binding_hash": persisted_d2.model_registry_binding_hash,
            "factor_risk_calculation_id": persisted_d2.factor_risk_calculation_id,
            "risk_free_manifest_id": persisted_d2.risk_free_manifest_id,
            "risk_free_curve_hash": persisted_d2.risk_free_curve_hash,
            "accounting_snapshot_id": persisted_d2.accounting_snapshot_id,
        }
        for field, authoritative_value in result_fields.items():
            if replay[field] != authoritative_value:
                errors.append(f"D2 {field} does not match the replayed persisted canonical bundle")
        for field in ("runtime_run_id", "as_of_utc", "information_cutoff_utc", "code_revision"):
            if lineage[field] != persisted_lineage[field]:
                errors.append(f"runtime lineage {field} does not match the persisted runtime record")
    if persisted_migrations is None or evidence["migration_versions"] != list(persisted_migrations):
        errors.append("migration_versions must match the authoritative applied migration inventory")
    for name in ("iam_policy_observation", "api_state_observation", "scheduler_inventory", "ingress_inventory", "recovery_observation"):
        if persisted_operations is None or persisted_operations.get(name) != evidence[name]:
            errors.append(f"{name} must match an externally attested persisted observation")

    try:
        observed_at = _instant(evidence["observed_at"])
        as_of = _instant(lineage["as_of_utc"])
        cutoff = _instant(lineage["information_cutoff_utc"])
        project_at = _instant(evidence["project"]["observed_at"])
        instance_at = _instant(instance["observed_at"])
        disk_at = _instant(disk["observed_at"])
        service_account_at = _instant(instance["attached_service_account"]["observed_at"])
        snapshot_at = _instant(snapshot["observed_at"])
        restore_at = _instant(restore_test["verified_at"])
        d2_at = _instant(replay["observed_at"])
        inventory_times = [_instant(evidence[name]["observed_at"])
                           for name in ("scheduler_inventory", "ingress_inventory", "iam_policy_observation", "api_state_observation", "recovery_observation")]
        immutable_until = _instant(snapshot["retention_policy"]["immutable_until"])
        retention_days = snapshot["retention_policy"]["minimum_retention_days"]
        now = _instant((clock or SystemClock()).now_utc())

        if cutoff > as_of:
            errors.append("runtime information_cutoff_utc must not be later than as_of_utc")
        if snapshot_at < disk_at:
            errors.append("backup snapshot must be observed at or after its source disk")
        if restore_at < snapshot_at:
            errors.append("restore verification must be observed at or after its snapshot")
        if d2_at < as_of:
            errors.append("canonical D2 replay observation must be at or after runtime as_of_utc")
        if any(value > observed_at for value in
               [project_at, instance_at, disk_at, service_account_at, snapshot_at,
                restore_at, d2_at, *inventory_times]):
            errors.append("deployment evidence observed_at precedes one of its evidence observations")
        if immutable_until < snapshot_at + timedelta(days=retention_days):
            errors.append("snapshot immutable_until does not satisfy its minimum retention period")
        if immutable_until <= observed_at:
            errors.append("snapshot retention must remain active at deployment evidence observed_at")
        if immutable_until <= now:
            errors.append("snapshot retention has expired at verification time")
        observations = [observed_at, project_at, instance_at, disk_at, service_account_at,
                        snapshot_at, restore_at, d2_at, *inventory_times, as_of]
        if any(value > now for value in observations):
            errors.append("evidence contains observations later than verification time")
        current_state = [observed_at, project_at, instance_at, disk_at, service_account_at,
                         d2_at, *inventory_times]
        if any(now - value > MAXIMUM_STATE_AGE for value in current_state):
            errors.append("current state observations exceed the one-hour freshness limit")
    except (TypeError, ValueError, OverflowError):
        errors.append("deployment evidence timestamps or retention duration cannot be verified")

    return sorted(set(errors))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="future deployment evidence JSON to validate")
    parser.add_argument("--database", type=Path, required=True,
                        help="existing canonical SQLite evidence store; opened read-only by replay")
    parser.add_argument("--immutable-store", type=Path, required=True,
                        help="existing immutable D2 source store; replay reads verified objects only")
    args = parser.parse_args(argv)

    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "schemas/application_runtime_deployment_evidence.schema.json").read_text(encoding="utf-8"))
        identity = json.loads((ROOT / "config/research_operations_identity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        print(json.dumps({
            "evidence_contract_valid": False,
            "deployment_authorized": False,
            "errors": [f"evidence inputs are unreadable or invalid JSON: {exc}"],
        }, sort_keys=True))
        return 2

    schema_errors = [error.message for error in
                     Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(evidence)] \
        if isinstance(evidence, dict) and isinstance(schema, dict) else ["evidence and schema must be JSON objects"]
    if schema_errors:
        errors = sorted(set(schema_errors))
    else:
        try:
            persisted_d2, persisted_lineage, persisted_migrations, persisted_operations = replay_persisted_d2(
                args.database, args.immutable_store, evidence["d2_replay"]["runtime_run_id"],
                observations={name: evidence[name] for name in ("iam_policy_observation", "api_state_observation")},
                inventories={name: evidence[name] for name in ("scheduler_inventory", "ingress_inventory")},
                instance_id=evidence["runtime_topology"]["instance"]["resource_id"],
                recovery_observation=evidence["recovery_observation"], instance=evidence["runtime_topology"]["instance"])
            errors = validate_evidence(
                evidence, schema, identity,
                persisted_d2=persisted_d2,
                persisted_lineage=persisted_lineage,
                persisted_migrations=persisted_migrations,
                persisted_operations=persisted_operations,
            )
        except (AssetManagementError, OSError, ValueError, sqlite3.DatabaseError,
                KeyError, TypeError, AttributeError, IndexError, RecursionError) as exc:
            errors = [f"persisted canonical D2 replay failed closed: {exc}"]

    print(json.dumps({
        "evidence_contract_valid": not errors,
        "deployment_authorized": False,
        "errors": errors,
    }, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
