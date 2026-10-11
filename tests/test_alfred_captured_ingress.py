"""Synthetic transport/replay mechanisms, not historical PIT or OOS evidence."""
import base64
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from asset_management.data.alfred import DATASET, INGESTION_SERIES, SOURCE
from asset_management.data.alfred_ingress import ENDPOINT, ingest_captured_alfred_response
from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.data.receipt_audit import audit_receipts
from asset_management.data.treasury_snapshot import TENORS, persist_treasury_quote_snapshot
from asset_management.time.asof import AsOfContext

RECEIVED = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
LICENSE = "purpose=synthetic-research;redistribution=forbidden;retention=perpetual"


def capture(store, series="DGS1", output_type=1, *, receipt_changes=None, response_changes=None, observations=None):
    if observations is None:
        observations = ([{"date": "2026-10-08", "realtime_start": "2026-10-09",
                          "realtime_end": "9999-12-31", "value": "."}]
                        if output_type == 1 else [{"date": "2026-10-08", f"{series}_20261009": "."}])
    body = dict(count=len(observations), offset=0, output_type=output_type, units="lin",
                realtime_start="2026-10-09", realtime_end="2026-10-09", observations=observations)
    body.update(response_changes or {})
    raw = canonical(body)
    receipt = dict(source=SOURCE, dataset=DATASET, endpoint=ENDPOINT, http_method="GET", mode="RESEARCH",
        status_code=200, request=dict(series_id=series, units="lin", file_type="json", output_type=output_type,
                                     realtime_start="2026-10-09", realtime_end="2026-10-09"),
        received_at=RECEIVED.isoformat(), http_date="Sat, 10 Oct 2026 12:00:00 GMT",
        body_base64=base64.b64encode(raw).decode(), body_sha256=digest(raw))
    receipt.update(receipt_changes or {})
    return store.catalog("unadmitted-http-responses", receipt)


def ingest(store, identifier):
    return ingest_captured_alfred_response(store, identifier, license_tag=LICENSE, code_revision="git:abcdef0")


@pytest.mark.parametrize("series", sorted(INGESTION_SERIES))
@pytest.mark.parametrize("output_type", [1, 3])
def test_complete_capture_uses_existing_adapter_and_preserves_tombstones(tmp_path, series, output_type):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    identifier = capture(store, series, output_type)
    result = ingest(store, identifier)
    assert result.status == "READY", result.reason_code
    assert ingest(store, identifier) == result
    bronze, raw = store.read(result.bronze_manifest_id)
    silver, rows = store.read(result.silver_manifest_id)
    assert raw["source_response_receipt"] == identifier
    assert raw["response"]["observations"]
    assert silver.parent_manifest_ids == (bronze.manifest_id,)
    assert rows[0]["series_id"] == series and rows[0]["value_status"] == "MISSING"
    assert silver.available_at == bronze.available_at == RECEIVED.isoformat()
    assert not audit_receipts(tmp_path, secrets=())["findings"]


@pytest.mark.parametrize("change", [
    {"source": "other"}, {"dataset": "other"}, {"mode": "LIVE"}, {"http_method": "POST"},
    {"endpoint": ENDPOINT + "?series_id=DTB3"}, {"status_code": True}, {"status_code": 429},
    {"http_date": None}, {"received_at": "2026-10-10T12:00:00"},
    {"http_date": "Sat, 10 Oct 2026 13:00:00 GMT"}, {"body_base64": "invalid!"},
    {"body_sha256": "0" * 64}, {"request": {"series_id": "DGS1", "file_type": "xml"}},
    {"request": {"series_id": "DTB3", "file_type": "json"}},
    {"request": {"series_id": "DGS1", "file_type": "json", "output_type": True}},
    {"request": {"series_id": "DGS1", "file_type": "json", "units": "pch"}},
])
def test_invalid_transport_never_publishes(tmp_path, change):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    with pytest.raises((ValueError, OSError)):
        ingest(store, capture(store, receipt_changes=change))
    assert not list((tmp_path / "bronze").glob("*.json"))
    assert not list((tmp_path / "silver").glob("*.json"))


@pytest.mark.parametrize("change", [{"units": "pch"}, {"output_type": True}, {"output_type": 3},
                                     {"password": "private-echo"}])
def test_response_semantics_and_sensitive_fields_are_not_sanitized_into_proof(tmp_path, change):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    with pytest.raises(ValueError): ingest(store, capture(store, response_changes=change))
    assert not list((tmp_path / "bronze").glob("*.json"))


def test_incomplete_page_keeps_raw_but_does_not_publish_silver(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    result = ingest(store, capture(store, response_changes={"count": 2}))
    assert result.status == "NO_TRADE" and result.reason_code == "ALFRED_INCOMPLETE_PAGE"
    assert result.bronze_manifest_id and result.silver_manifest_id is None


def clone(store, manifest, body, **changes):
    values = dict(layer=manifest.layer, source=manifest.source, dataset=manifest.dataset,
        schema_version=manifest.schema_version, retrieved_at=RECEIVED, available_at=RECEIVED,
        provider_timestamp=RECEIVED, license_tag=manifest.license_tag, code_revision=manifest.code_revision,
        request_hash=manifest.request_hash, quality_status=manifest.quality_status,
        parent_manifest_ids=manifest.parent_manifest_ids)
    values.update(changes)
    return store.write(body, **values)


@pytest.mark.parametrize("field,value", [("value", "42"), ("output_type", True), ("series_id", "DGS2")])
def test_rehashed_silver_is_replayed_against_captured_bytes(tmp_path, field, value):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    result = ingest(store, capture(store))
    manifest, rows = store.read(result.silver_manifest_id)
    rows[0][field] = value
    bad = clone(store, manifest, rows)
    with pytest.raises(ValueError, match="ALFRED_CAPTURE_NORMALIZATION_MISMATCH"):
        store.read(bad.manifest_id)


@pytest.mark.parametrize("forgery", ["body", "request", "time"])
def test_rehashed_bronze_cannot_detach_from_receipt(tmp_path, forgery):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    result = ingest(store, capture(store))
    manifest, raw = store.read(result.bronze_manifest_id)
    changes = {}
    if forgery == "body": raw["response"]["observations"][0]["value"] = "42"
    if forgery == "request":
        request = store.read_catalog("requests", manifest.request_hash)
        request["request"]["series_id"] = "DGS2"
        changes["request_hash"] = store.catalog("requests", request)
    if forgery == "time": changes["available_at"] = NOW
    bad = clone(store, manifest, raw, **changes)
    with pytest.raises(ValueError, match="ALFRED_CAPTURE_LINEAGE_CONFLICT"): store.read(bad.manifest_id)


def test_source_loss_blocks_bronze_silver_gold_and_receipt_audit(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    manifests, results, captures = {}, {}, {}
    for series in TENORS:
        captures[series] = capture(store, series)
        results[series] = ingest(store, captures[series])
        manifests[series] = results[series].silver_manifest_id
    context = AsOfContext("capture-mechanism", NOW, NOW, "research-policy", "test-spec", "git:abcdef0")
    gold = persist_treasury_quote_snapshot(store=store, manifests=manifests,
        observation_date="2026-10-08", vintage_date="2026-10-09", context=context)
    assert store.read(gold.manifest_id)[1]["status"] == "MISSING"
    path = store.layout.resolve("catalog", f"unadmitted-http-responses/{captures['DGS1']}.json")
    path.unlink()
    for identifier in (results["DGS1"].bronze_manifest_id, manifests["DGS1"], gold.manifest_id):
        with pytest.raises(OSError): store.read(identifier)
    assert audit_receipts(tmp_path, secrets=())["findings"]


def test_canonical_macro_uses_captured_silver_without_changing_required_series(tmp_path):
    from test_ama178_canonical_macro_state import fixture_rows, context
    from asset_management.features.macro_state import MacroStateSpec, run_macro_state
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    manifests = {}
    rows = fixture_rows()
    for series in ("DGS2", "DGS10"):
        rows[series][0].update(date="2026-10-08", realtime_start="2026-10-09")
    for series, observations in rows.items():
        manifests[series] = ingest(store, capture(store, series, observations=observations)).silver_manifest_id
    spec = MacroStateSpec()
    ctx = replace(context(spec), as_of_utc=NOW, information_cutoff_utc=NOW)
    assert run_macro_state(spec, store=store, manifests=manifests, context=ctx).payload()["status"] == "COMPUTED"


def test_classified_credentials_and_valid_revision_required(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    identifier = capture(store)
    with pytest.raises(ValueError, match="CREDENTIALS_NOT_CLASSIFIED"):
        ingest(ImmutableDatasetStore(tmp_path), identifier)
    with pytest.raises(ValueError, match="INVALID_CODE_REVISION"):
        ingest_captured_alfred_response(store, identifier, license_tag=LICENSE, code_revision="local")


def test_duplicate_json_keys_cannot_silently_change_source_meaning(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    identifier = capture(store)
    receipt = store.read_catalog("unadmitted-http-responses", identifier)
    raw = b'{"units":"lin","units":"pch","output_type":1}'
    receipt.update(body_base64=base64.b64encode(raw).decode(), body_sha256=digest(raw))
    with pytest.raises(ValueError, match="ALFRED_DUPLICATE_JSON_KEY"):
        ingest(store, store.catalog("unadmitted-http-responses", receipt))
    assert not list((tmp_path / "bronze").glob("*.json"))


def test_special_dispatch_is_scoped_to_exact_source_dataset_and_schema(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    manifest, raw = store.read(ingest(store, capture(store)).bronze_manifest_id)
    raw["source_response_receipt"] = "0" * 64
    unrelated = clone(store, manifest, raw, source="other-source")
    assert store.read(unrelated.manifest_id)[1] == raw
