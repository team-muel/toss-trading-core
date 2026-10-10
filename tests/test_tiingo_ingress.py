import base64

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, digest
from asset_management.data.tiingo_ingress import admit_captured_tiingo_response


def capture(store, **changes):
    body = b'[{"close":100}]'
    receipt = {"source":"tiingo-eod", "dataset":"daily-prices", "http_method":"GET",
        "mode":"RESEARCH", "endpoint":"/tiingo/daily/SPY/prices", "request":{"symbol":"SPY"},
        "status_code":200, "body_base64":base64.b64encode(body).decode(), "body_sha256":digest(body),
        "received_at":"2026-10-11T00:00:01+00:00", "http_date":"Sun, 11 Oct 2026 00:00:00 GMT"}
    receipt.update(changes)
    return store.catalog("unadmitted-http-responses", receipt)


def admit(store, identifier):
    return admit_captured_tiingo_response(store, identifier,
        license_tag="purpose=synthetic;redistribution=forbidden;retention=perpetual",
        code_revision="git:abcdef0")


def test_bronze_only_idempotent_and_request_bound(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    identifier = capture(store)
    first = admit(store, identifier)
    assert admit(store, identifier) == first
    assert first.layer == "bronze" and first.quality_status == "RAW"
    _, body = store.read(first.manifest_id)
    assert body["source_response_receipt"] == identifier
    assert not list((tmp_path/"silver").glob("*.json"))


@pytest.mark.parametrize("changes", [
    {"body_sha256":"0"*64}, {"status_code":True}, {"status_code":403},
    {"mode":"LIVE"}, {"source":"other"}, {"endpoint":"https://api.tiingo.com/prices"},
    {"request":[]}, {"received_at":"2026-10-11T00:00:01"},
    {"http_date":"Sun, 11 Oct 2026 00:00:02 GMT"},
])
def test_invalid_capture_never_publishes_bronze(tmp_path, changes):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    with pytest.raises(ValueError):
        admit(store, capture(store, **changes))
    assert not list((tmp_path/"bronze").glob("*.json"))


def test_unclassified_credentials_rejected(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    with pytest.raises(ValueError, match="CREDENTIALS_NOT_CLASSIFIED"):
        admit(store, capture(store))


@pytest.mark.parametrize("field", ["body_base64", "body_sha256", "received_at", "http_date"])
@pytest.mark.parametrize("value", [None, "", [], 123])
def test_capture_fields_typed_and_required(tmp_path, field, value):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    with pytest.raises(ValueError, match="TIINGO_CAPTURE_FIELDS_REQUIRED"):
        admit(store, capture(store, **{field:value}))
    assert not list((tmp_path/"bronze").glob("*.json"))


def test_decoded_body_echoed_secret_rejected(tmp_path):
    store = ImmutableDatasetStore(tmp_path, secrets=("private-value",), credentials_classified=True)
    body = b'[{"note":"private-value"}]'
    identifier = capture(store, body_base64=base64.b64encode(body).decode(), body_sha256=digest(body))
    with pytest.raises(ValueError):
        admit(store, identifier)
    assert not list((tmp_path/"bronze").glob("*.json"))


@pytest.mark.parametrize("captured_request", [{"symbol":"AAPL"}, {"params":{"ticker":"AAPL"}},
                                      {"params":{"symbol":None}}, {"params":[]}])
def test_conflicting_request_symbol_rejected(tmp_path, captured_request):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    with pytest.raises(ValueError):
        admit(store, capture(store, request=captured_request))
    assert not list((tmp_path/"bronze").glob("*.json"))


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_later_source_receipt_loss_blocks_read_and_audit(tmp_path, damage):
    from asset_management.data.receipt_audit import audit_receipts
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    identifier = capture(store); manifest = admit(store, identifier)
    path = store.layout.resolve("catalog", f"unadmitted-http-responses/{identifier}.json")
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{}")
    with pytest.raises((ValueError, OSError)):
        store.read(manifest.manifest_id)
    report = audit_receipts(tmp_path, secrets=())
    assert report["verified_manifests_by_layer"] == {}
    assert report["findings"][0]["reason"] == "MANIFEST_OR_LINEAGE_INVALID"


@pytest.mark.parametrize("field", ["token", "api_key", "Authorization"])
def test_sensitive_key_echo_never_publishes_unreplayable_bronze(tmp_path, field):
    import json
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    body = json.dumps([{field:"short-secret"}]).encode()
    identifier = capture(store, body_base64=base64.b64encode(body).decode(), body_sha256=digest(body))
    with pytest.raises(ValueError, match="TIINGO_UNREDACTED_RESPONSE"):
        admit(store, identifier)
    assert not list((tmp_path/"bronze").glob("*.json"))


def test_unrelated_provider_payload_key_remains_readable(tmp_path):
    from datetime import datetime, timezone
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    now = datetime(2026,10,11,tzinfo=timezone.utc)
    request = store.catalog("requests", {"source":"other", "dataset":"custom",
        "endpoint":"/custom", "http_method":"GET", "request":{}})
    body = {"source_response_receipt":"arbitrary provider field", "value":1}
    manifest = store.write(body, layer="bronze", source="other", dataset="custom",
        schema_version="custom-v1", retrieved_at=now, available_at=now, provider_timestamp=now,
        license_tag="purpose=synthetic;redistribution=forbidden;retention=perpetual",
        code_revision="git:abcdef0", request_hash=request, quality_status="RAW")
    assert store.read(manifest.manifest_id)[1] == body
