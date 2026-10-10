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


@pytest.mark.parametrize("unsafe_request", [{"Authorization": "Bearer synthetic-private-value"},
    {"api_key": "dummy-private"}, {"note": "api-abcdefghijklmnop1234"}])
def test_imported_credential_receipt_is_blocked(tmp_path, unsafe_request):
    from asset_management.data.immutable import canonical, digest
    store, first = bronze(tmp_path)
    receipt = {"source": "provider", "dataset": "prices", "endpoint": "/prices",
               "http_method": "GET", "request": unsafe_request}
    raw = canonical(receipt); identifier = digest(raw)
    store._publish(store.layout.resolve("catalog", f"requests/{identifier}.json"), raw)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    store.write([{"value": 2}], layer="bronze", source="provider", dataset="prices",
        schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag=first.license_tag, code_revision="git:abcdef0", request_hash=identifier,
        quality_status="RAW")
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["findings"][0]["reason"] == "REQUEST_RECEIPT_MISSING_OR_CORRUPT"
    assert "synthetic-private-value" not in str(report)


@pytest.mark.parametrize("layer", ["silver", "gold"])
@pytest.mark.parametrize("invalid_hash", [None, [], "", "a" * 63])
def test_derived_request_hash_contract_is_revalidated(tmp_path, layer, invalid_hash):
    import json
    from asset_management.data.immutable import canonical, digest
    store, original = bronze(tmp_path)
    fields = json.loads(store.layout.resolve("catalog", f"manifests/{original.manifest_id}.json").read_bytes())
    fields.pop("manifest_id")
    fields.update(layer=layer, quality_status="VALID", parent_manifest_ids=[original.manifest_id],
                  request_hash=invalid_hash)
    identifier = digest(canonical(fields)); fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    with pytest.raises(ValueError, match="INVALID_REQUEST_HASH"):
        store.read_manifest(identifier)
    assert audit_receipts(tmp_path)["verified_manifests_by_layer"] == {"bronze": 1}


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
    with pytest.raises(ValueError, match="INVALID_REQUEST_HASH"):
        store.write([{"value": 2}], layer="silver", source="provider", dataset="prices",
            schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
            license_tag=original.license_tag, code_revision="git:abcdef0",
            request_hash=original.request_hash, parent_manifest_ids=(identifier,))
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["blocked_manifests_with_unverified_layer"] == 1
    assert len(report["findings"]) == 1


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



def test_corrupt_bronze_blob_counts_blocked_descendant_layers(tmp_path):
    store, parent = bronze(tmp_path)
    first = parent
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    for layer in ("silver", "gold"):
        parent = store.write([{"value": 1}], layer=layer, source="provider", dataset="prices",
            schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
            license_tag=parent.license_tag, code_revision="git:abcdef0", request_hash=parent.request_hash,
            parent_manifest_ids=(parent.manifest_id,))
    store.layout.resolve("bronze", f"{first.content_sha256}.json").write_text("[]")
    report = audit_receipts(tmp_path)
    assert report["blocked_manifests_by_layer"] == {"bronze": 1, "silver": 1, "gold": 1}
    assert report["verified_manifests_by_layer"] == {}
    assert report["blocked_manifests_with_unverified_layer"] == 0
    assert len(report["findings"]) == 3


@pytest.mark.parametrize("parents", [[[]], [{}], [None], [123], ["invalid"], None, "invalid"])
def test_malformed_parent_ids_are_reported_without_propagation_crash(tmp_path, parents):
    import json
    from asset_management.data.immutable import canonical, digest
    store, original = bronze(tmp_path)
    path = store.layout.resolve("catalog", f"manifests/{original.manifest_id}.json")
    fields = json.loads(path.read_bytes())
    fields.pop("manifest_id")
    fields["parent_manifest_ids"] = parents
    identifier = digest(canonical(fields))
    fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["blocked_manifests_with_unverified_layer"] == 1
    assert report["findings"] == [{"manifest_id": identifier, "reason": "MANIFEST_OR_LINEAGE_INVALID"}]


@pytest.mark.parametrize("layer", ["silver", "gold"])
def test_derived_without_parents_is_not_verified(tmp_path, layer):
    import json
    from asset_management.data.immutable import canonical, digest
    store, original = bronze(tmp_path)
    fields = json.loads(store.layout.resolve("catalog", f"manifests/{original.manifest_id}.json").read_bytes())
    fields.pop("manifest_id")
    fields["layer"] = layer
    identifier = digest(canonical(fields))
    fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    store._publish(store.layout.resolve(layer, f"{original.content_sha256}.json"), canonical([{"value": 1}]))
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["findings"] == [{"manifest_id": identifier, "reason": "MANIFEST_OR_LINEAGE_INVALID"}]


def test_read_rejects_same_layer_parent(tmp_path):
    import json
    from asset_management.data.immutable import canonical, digest
    store, first = bronze(tmp_path)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    parent = store.write([{"value": 1}], layer="silver", source="provider", dataset="prices",
        schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag=first.license_tag, code_revision="git:abcdef0", request_hash=first.request_hash,
        parent_manifest_ids=(first.manifest_id,))
    fields = json.loads(store.layout.resolve("catalog", f"manifests/{parent.manifest_id}.json").read_bytes())
    fields.pop("manifest_id")
    fields["parent_manifest_ids"] = [parent.manifest_id]
    identifier = digest(canonical(fields))
    fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    with pytest.raises(ValueError, match="INVALID_LINEAGE_LAYER"):
        store.read(identifier)
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1, "silver": 1}
    assert report["blocked_manifests_by_layer"] == {"silver": 1}



def test_read_rejects_child_available_before_parent(tmp_path):
    import json
    from asset_management.data.immutable import canonical, digest
    store, first = bronze(tmp_path)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    child = store.write([{"value": 1}], layer="silver", source="provider", dataset="prices",
        schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag=first.license_tag, code_revision="git:abcdef0", request_hash=first.request_hash,
        parent_manifest_ids=(first.manifest_id,))
    fields = json.loads(store.layout.resolve("catalog", f"manifests/{child.manifest_id}.json").read_bytes())
    fields.pop("manifest_id")
    for key in ("retrieved_at", "available_at", "provider_timestamp"):
        fields[key] = "2026-10-09T00:00:00+00:00"
    identifier = digest(canonical(fields))
    fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    with pytest.raises(ValueError, match="PARENT_NOT_AVAILABLE"):
        store.read(identifier)
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1, "silver": 1}
    assert report["blocked_manifests_by_layer"] == {"silver": 1}


@pytest.mark.parametrize("field,value", [("available_at", "2026-10-09T00:00:00+00:00"),
    ("provider_timestamp", "2026-10-11T00:00:00+00:00"),
    ("retrieved_at", "2026-10-10T00:00:00"), ("available_at", None), ("available_at", "invalid")])
def test_invalid_manifest_timestamp_contract_is_blocked(tmp_path, field, value):
    import json
    from asset_management.data.immutable import canonical, digest
    store, original = bronze(tmp_path)
    fields = json.loads(store.layout.resolve("catalog", f"manifests/{original.manifest_id}.json").read_bytes())
    fields.pop("manifest_id")
    fields[field] = value
    identifier = digest(canonical(fields))
    fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["findings"] == [{"manifest_id": identifier, "reason": "MANIFEST_OR_LINEAGE_INVALID"}]


@pytest.mark.parametrize("timestamp", ["2026-10-10T09:00:00+09:00", "2026-10-10T00:00:00Z",
    "2026-10-10T00:00:00.000000+00:00"])
def test_noncanonical_utc_manifest_is_blocked(tmp_path, timestamp):
    import json
    from asset_management.data.immutable import canonical, digest
    store, original = bronze(tmp_path)
    fields = json.loads(store.layout.resolve("catalog", f"manifests/{original.manifest_id}.json").read_bytes())
    fields.pop("manifest_id")
    for field in ("retrieved_at", "available_at", "provider_timestamp"):
        fields[field] = timestamp
    identifier = digest(canonical(fields)); fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["findings"][0]["reason"] == "MANIFEST_OR_LINEAGE_INVALID"


@pytest.mark.parametrize("layer,changes,reason", [
    ("silver", {"source": "other"}, "CONFLICTING_SOURCE_OR_LICENSE"),
    ("silver", {"license_tag": "purpose=synthetic;redistribution=permitted;retention=perpetual"}, "CONFLICTING_SOURCE_OR_LICENSE"),
    ("gold", {"license_tag": "purpose=synthetic;redistribution=permitted;retention=perpetual"}, "COMBINED_LICENSE_WEAKENS_PARENT")])
def test_read_preserves_parent_source_and_license(tmp_path, layer, changes, reason):
    import json
    from asset_management.data.immutable import canonical, digest
    store, first = bronze(tmp_path)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    child = store.write([{"value": 1}], layer=layer, source="provider", dataset="prices",
        schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag=first.license_tag, code_revision="git:abcdef0", request_hash=first.request_hash,
        parent_manifest_ids=(first.manifest_id,))
    fields = json.loads(store.layout.resolve("catalog", f"manifests/{child.manifest_id}.json").read_bytes())
    fields.pop("manifest_id"); fields.update(changes)
    identifier = digest(canonical(fields)); fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    with pytest.raises(ValueError, match=reason): store.read(identifier)
    report = audit_receipts(tmp_path)
    assert report["blocked_manifests_by_layer"] == {layer: 1}


@pytest.mark.parametrize("raw_receipt", [
    b'{"source":"other","source":"provider","dataset":"prices","endpoint":"/prices","http_method":"GET","request":{}}',
    b'{"dataset":"prices","endpoint":"/prices","http_method":"GET","request":{"symbol":"B","symbol":"A"},"source":"provider"}',
    b'{ "dataset": "prices", "endpoint": "/prices", "http_method": "GET", "request": {}, "source": "provider" }'])
def test_noncanonical_receipt_bytes_are_blocked(tmp_path, raw_receipt):
    from asset_management.data.immutable import digest
    store, first = bronze(tmp_path)
    identifier = digest(raw_receipt)
    store._publish(store.layout.resolve("catalog", f"requests/{identifier}.json"), raw_receipt)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    store.write([{"value": 2}], layer="bronze", source="provider", dataset="prices",
        schema_version="test-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag=first.license_tag, code_revision="git:abcdef0", request_hash=identifier,
        quality_status="RAW")
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["blocked_manifests_by_layer"] == {"bronze": 1}
    assert report["findings"][0]["reason"] == "REQUEST_RECEIPT_MISSING_OR_CORRUPT"


@pytest.mark.parametrize("field,value", [("source", None), ("source", ""), ("dataset", []),
    ("schema_version", ""), ("code_revision", None), ("quality_status", "UNKNOWN"),
    ("row_count", True), ("row_count", -1), ("row_count", 2)])
def test_invalid_manifest_metadata_and_row_count_is_blocked(tmp_path, field, value):
    import json
    from asset_management.data.immutable import canonical, digest
    store, original = bronze(tmp_path)
    fields = json.loads(store.layout.resolve("catalog", f"manifests/{original.manifest_id}.json").read_bytes())
    fields.pop("manifest_id"); fields[field] = value
    identifier = digest(canonical(fields)); fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["findings"][0]["reason"] == "MANIFEST_OR_LINEAGE_INVALID"


@pytest.mark.parametrize("encoding", ["duplicate", "pretty"])
def test_noncanonical_manifest_bytes_are_blocked(tmp_path, encoding):
    import json
    store, original = bronze(tmp_path)
    path = store.layout.resolve("catalog", f"manifests/{original.manifest_id}.json")
    content = path.read_bytes()
    if encoding == "duplicate": content = b'{"source":"other",' + content[1:]
    else: content = json.dumps(json.loads(content), indent=2).encode()
    path.write_bytes(content)
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {}
    assert report["findings"][0]["reason"] == "MANIFEST_OR_LINEAGE_INVALID"


def test_hash_addressed_noncanonical_blob_is_not_verified(tmp_path):
    import json
    from asset_management.data.immutable import canonical, digest
    store, original = bronze(tmp_path)
    raw = b'{"value":1,"value":2}'
    content_hash = digest(raw)
    store._publish(store.layout.resolve("bronze", f"{content_hash}.json"), raw)
    fields = json.loads(store.layout.resolve("catalog", f"manifests/{original.manifest_id}.json").read_bytes())
    fields.pop("manifest_id"); fields["content_sha256"] = content_hash
    identifier = digest(canonical(fields)); fields["manifest_id"] = identifier
    store._publish(store.layout.resolve("catalog", f"manifests/{identifier}.json"), canonical(fields))
    report = audit_receipts(tmp_path)
    assert report["verified_manifests_by_layer"] == {"bronze": 1}
    assert report["blocked_manifests_by_layer"] == {"bronze": 1}
