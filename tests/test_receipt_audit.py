from datetime import datetime, timezone

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.data.receipt_audit import audit_receipts


def bronze(root):
    store = ImmutableDatasetStore(root, credentials_classified=True)
    receipt = store.catalog("requests", {"source": "provider", "dataset": "prices"})
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
    assert sum(x["reason"] == "ANCESTOR_RECEIPT_OR_LINEAGE_INVALID" for x in report["findings"]) == 2
