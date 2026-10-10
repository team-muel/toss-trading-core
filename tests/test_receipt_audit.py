from datetime import datetime, timezone

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.data.receipt_audit import audit_receipts


def bronze(root):
    store = ImmutableDatasetStore(root, credentials_classified=True)
    receipt = store.catalog("requests", {"source": "provider", "dataset": "prices",
        "endpoint": "/prices", "http_method": "GET", "request": {"symbol": "A"}})
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    manifest = store.write([{"value": 1}], layer="bronze", source="provider", dataset="prices",
        schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag="purpose=synthetic;redistribution=forbidden;retention=perpetual",
        code_revision="git:abcdef0", request_hash=receipt, quality_status="RAW")
    return store, manifest


def test_valid_local_storage_is_not_acceptance(tmp_path):
    bronze(tmp_path)
    report = audit_receipts(tmp_path)
    assert report["findings"] == []
    assert report["acceptance_evaluated"] is False
    assert report["manifests_scanned"] == 1


@pytest.mark.parametrize("damage", ["missing", "corrupt", "blob"])
def test_audit_reports_damage_without_mutating_store(tmp_path, damage):
    store, manifest = bronze(tmp_path)
    path = store.layout.resolve("catalog", f"requests/{manifest.request_hash}.json")
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_text("{}")
    else:
        store.layout.resolve("bronze", f"{manifest.content_sha256}.json").write_text("[]")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")}
    report = audit_receipts(tmp_path)
    assert len(report["findings"]) == 1
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")} == before


def test_missing_store_is_not_a_clean_audit(tmp_path):
    assert audit_receipts(tmp_path)["findings"][0]["reason"] == "MANIFEST_CATALOG_MISSING_OR_EMPTY"


def test_missing_bronze_receipt_blocks_silver_and_gold(tmp_path):
    store, parent = bronze(tmp_path)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    for layer in ("silver", "gold"):
        parent = store.write([{"value": 1}], layer=layer, source="provider", dataset="prices",
            schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
            license_tag=parent.license_tag, code_revision="git:abcdef0", request_hash=parent.request_hash,
            parent_manifest_ids=(parent.manifest_id,))
    store.layout.resolve("catalog", f"requests/{parent.request_hash}.json").unlink()
    report = audit_receipts(tmp_path)
    assert report["blocked_manifests_by_layer"] == {"bronze": 1, "gold": 1, "silver": 1}
    assert report["verified_manifests_by_layer"] == {}
    assert sum(x["reason"] == "ANCESTOR_RECEIPT_OR_LINEAGE_INVALID" for x in report["findings"]) == 2


@pytest.mark.parametrize("field,value", [("endpoint", None), ("endpoint", "https://provider"),
    ("http_method", "DELETE"), ("http_method", []), ("http_method", True), ("request", None), ("request", "symbol=A")])
def test_incomplete_request_contract_is_blocked(tmp_path, field, value):
    store, original = bronze(tmp_path)
    receipt = store.read_catalog("requests", original.request_hash)
    receipt[field] = value
    request_hash = store.catalog("requests", receipt)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    store.write([{"value": 2}], layer="bronze", source="provider", dataset="prices",
        schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag=original.license_tag, code_revision="git:abcdef0",
        request_hash=request_hash, quality_status="RAW")
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["blocked_manifests_by_layer"] == {"bronze": 1}
    assert report["findings"][0]["reason"] == "REQUEST_RECEIPT_CONTRACT_INCOMPLETE"


def test_source_dataset_only_receipt_is_not_verified(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    request_hash = store.catalog("requests", {"source": "provider", "dataset": "prices"})
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    store.write([{"value": 1}], layer="bronze", source="provider", dataset="prices",
        schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag="purpose=synthetic;redistribution=forbidden;retention=perpetual",
        code_revision="git:abcdef0", request_hash=request_hash, quality_status="RAW")
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {}
    assert report["findings"][0]["reason"] == "REQUEST_RECEIPT_CONTRACT_INCOMPLETE"


@pytest.mark.parametrize("request_hash", [None, [], 123])
def test_typed_invalid_manifest_request_hash_blocks_descendants(tmp_path, request_hash):
    import json
    from asset_management.data.immutable import canonical, digest
    store, original = bronze(tmp_path)
    path = store.layout.resolve("catalog", f"manifests/{original.manifest_id}.json")
    fields = json.loads(path.read_bytes())
    fields.pop("manifest_id")
    fields["request_hash"] = request_hash
    identifier = digest(canonical(fields))
    fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    store.write([{"value": 2}], layer="silver", source="provider", dataset="prices",
        schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag=original.license_tag, code_revision="git:abcdef0",
        request_hash=original.request_hash, parent_manifest_ids=(identifier,))
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["blocked_manifests_by_layer"] == {"bronze": 1, "silver": 1}
    assert {x["reason"] for x in report["findings"]} == {
        "REQUEST_RECEIPT_MISSING_OR_CORRUPT", "ANCESTOR_RECEIPT_OR_LINEAGE_INVALID"}


@pytest.mark.parametrize("invalid_json", ["null", '"invalid"', "123", "true", "[]"])
def test_nonobject_manifest_is_reported_without_aborting(tmp_path, invalid_json):
    store, manifest = bronze(tmp_path)
    path = store.layout.resolve("catalog", f"manifests/{manifest.manifest_id}.json")
    path.write_text(invalid_json)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")}
    report = audit_receipts(tmp_path)
    assert report["findings"] == [{"manifest_id": manifest.manifest_id,
                                  "reason": "MANIFEST_OR_LINEAGE_INVALID"}]
    assert report["verified_manifests_by_layer"] == {}
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")} == before
