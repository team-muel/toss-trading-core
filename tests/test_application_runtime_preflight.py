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
