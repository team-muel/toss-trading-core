from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from asset_management.data.adapters.toss import TossDatasetAdapter
from asset_management.data.immutable import ImmutableDatasetStore, ProviderDatasetAdapter
from toss_trading.broker.credentials import TossCredentials
from toss_trading.broker.toss import TossApiResult, TossReadOnlyAdapter


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


def normalize(raw):
    return [{"provider_instrument_id": raw["symbol"], "price": raw["price"]}]


def revised(raw):
    return [{"provider_instrument_id": raw["symbol"], "price": raw["price"]}]


def broken(raw):
    raise ValueError("secret-password")


def kwargs():
    return dict(body={"symbol": "A", "price": "10.25", "API-Key": "secret-password"},
                request={"authorization": "secret-password", "symbol": "A"},
                endpoint="/api/v1/prices", http_method="GET",
                source="provider", dataset="prices", retrieved_at=NOW,
                available_at=NOW, provider_timestamp=NOW - timedelta(seconds=1),
                license_tag="purpose=internal-use;redistribution=forbidden;retention=perpetual",
                code_revision="git:abc1234", raw_schema={"symbol": "string", "price": "string"},
                schema_version="prices-v1", schema={"provider_instrument_id": "string", "price": "string"},
                instrument_mapping={"A": "instrument-A"}, normalize=normalize)


def test_raw_first_lineage_utc_secrets_and_dedup(tmp_path):
    store = ImmutableDatasetStore(tmp_path, secrets=("secret-password",), credentials_classified=True)
    adapter = ProviderDatasetAdapter(store)
    params = kwargs()
    params["retrieved_at"] = NOW.astimezone(timezone(timedelta(hours=9)))
    first = adapter.ingest(**params)
    assert first.status == "READY"
    assert adapter.ingest(**params) == first
    silver, rows = store.read(first.silver_manifest_id)
    bronze, raw = store.read(first.bronze_manifest_id)
    assert rows[0]["instrument_id"] == "instrument-A"
    assert raw["API-Key"] == "***REDACTED***"
    assert silver.parent_manifest_ids == (bronze.manifest_id,)
    assert bronze.retrieved_at == NOW.isoformat()
    assert silver.row_count == 1 and "purpose=internal-use" in silver.license_tag
    assert len(list((tmp_path / "bronze").glob("*.json"))) == 1
    assert "secret-password" not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert list((tmp_path / "catalog" / "schemas").glob("*.json"))


@pytest.mark.parametrize("changes,reason", [
    ({"normalize": broken}, "NORMALIZATION_FAILED"),
    ({"instrument_mapping": {}}, "UNKNOWN_INSTRUMENT"),
    ({"schema": {"missing": "string"}}, "SCHEMA_VALIDATION_FAILED"),
    ({"status_code": 503}, "PROVIDER_HTTP_ERROR"),
    ({"schema": {}}, "SCHEMA_VALIDATION_FAILED"),
])
def test_failures_preserve_raw_and_health(tmp_path, changes, reason):
    store = ImmutableDatasetStore(tmp_path, secrets=("secret-password",), credentials_classified=True)
    result = ProviderDatasetAdapter(store).ingest(**(kwargs() | changes))
    assert result.status == "NO_TRADE" and result.reason_code == reason
    assert store.read(result.bronze_manifest_id)[1]["symbol"] == "A"
    assert result.silver_manifest_id is None
    health = [json.loads(p.read_bytes()) for p in (tmp_path / "catalog" / "source-health").glob("*.json")]
    assert health[0]["status"] == "BLOCKED" and health[0]["reason_code"] == reason
    assert "secret-password" not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))


def test_revisions_preserve_all_history(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    adapter = ProviderDatasetAdapter(store)
    original = adapter.ingest(**kwargs())
    code = adapter.ingest(**(kwargs() | {"normalize": revised}))
    provider = adapter.ingest(**(kwargs() | {"body": {"symbol": "A", "price": "11.00"}}))
    assert original.bronze_manifest_id == code.bronze_manifest_id
    assert original.silver_manifest_id != code.silver_manifest_id
    assert original.bronze_manifest_id != provider.bronze_manifest_id
    assert store.read(original.silver_manifest_id)[1][0]["price"] == "10.25"
    assert store.read(provider.silver_manifest_id)[1][0]["price"] == "11.00"


def test_tamper_is_detected_and_cannot_overwrite(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    result = ProviderDatasetAdapter(store).ingest(**kwargs())
    bronze, _ = store.read(result.bronze_manifest_id)
    path = tmp_path / "bronze" / f"{bronze.content_sha256}.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="CONTENT_HASH_MISMATCH"):
        store.read(result.silver_manifest_id)
    retry = ProviderDatasetAdapter(store).ingest(**kwargs())
    assert retry.status == "NO_TRADE"
    assert path.read_text() == "{}"


def test_concurrent_publication_and_reopen(tmp_path):
    def ingest(_):
        return ProviderDatasetAdapter(ImmutableDatasetStore(tmp_path, credentials_classified=True)).ingest(**kwargs())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(ingest, range(12)))
    assert all(r == results[0] and r.status == "READY" for r in results)
    assert ImmutableDatasetStore(tmp_path, credentials_classified=True).read(results[0].silver_manifest_id)[1]


@pytest.mark.parametrize("changes", [
    {"license_tag": ""}, {"code_revision": ""},
    {"retrieved_at": NOW.replace(tzinfo=None)},
    {"provider_timestamp": NOW + timedelta(days=1)},
    {"available_at": NOW - timedelta(days=1)},
])
def test_unknown_metadata_fails_closed(tmp_path, changes):
    result = ProviderDatasetAdapter(ImmutableDatasetStore(tmp_path, credentials_classified=True)).ingest(**(kwargs() | changes))
    assert result.status == "NO_TRADE" and result.silver_manifest_id is None


def test_manifest_tamper_and_path_escape(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    result = ProviderDatasetAdapter(store).ingest(**kwargs())
    path = tmp_path / "catalog" / "manifests" / f"{result.silver_manifest_id}.json"
    content = json.loads(path.read_bytes())
    content["license_tag"] = "changed"
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="MANIFEST_HASH_MISMATCH"):
        store.read(result.silver_manifest_id)
    with pytest.raises(ValueError):
        store.read("../../escape")


def test_gold_lineage_and_missing_conflicting_parents(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    result = ProviderDatasetAdapter(store).ingest(**kwargs())
    silver, _ = store.read(result.silver_manifest_id)
    params = dict(layer="gold", source=silver.source, dataset="features", schema_version="features-v1",
                  retrieved_at=NOW, available_at=NOW, provider_timestamp=NOW,
                  license_tag=silver.license_tag, code_revision="git:abc1234",
                  request_hash=silver.request_hash, parent_manifest_ids=(silver.manifest_id,))
    gold = store.write([{"feature": "10.25"}], **params)
    assert store.read(gold.manifest_id)[0].parent_manifest_ids == (silver.manifest_id,)
    for change in ({"parent_manifest_ids": ()}, {"parent_manifest_ids": ("0" * 64,)},
                   {"license_tag": "unrestricted"}, {"parent_manifest_ids": (gold.manifest_id,)}):
        with pytest.raises((ValueError, FileNotFoundError)):
            store.write([{"feature": "10.25"}], **(params | change))
    with pytest.raises(ValueError, match="EMPTY_DERIVED_DATASET"):
        store.write([], **params)


def test_manifest_schema_matches_serialized_contract(tmp_path):
    from dataclasses import asdict
    from pathlib import Path
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    result = ProviderDatasetAdapter(store).ingest(**kwargs())
    schema = json.loads((Path(__file__).parents[1] / "schemas/dataset_manifest.schema.json").read_bytes())
    assert set(schema["required"]) == set(asdict(store.read(result.silver_manifest_id)[0]))


def test_catalog_paths_reject_parent_leaf(tmp_path):
    from asset_management.data.layout import DataLakeLayout
    with pytest.raises(ValueError):
        DataLakeLayout(tmp_path).resolve("catalog", "..")


def test_request_identity_raw_schema_and_credential_classification_fail_closed(tmp_path):
    base = kwargs()
    unclassified = ProviderDatasetAdapter(ImmutableDatasetStore(tmp_path / "a"))
    assert unclassified.ingest(**base).reason_code == "CREDENTIALS_NOT_CLASSIFIED"
    classified = ProviderDatasetAdapter(ImmutableDatasetStore(
        tmp_path / "b", credentials_classified=True))
    for changes in ({"endpoint": "prices"}, {"http_method": "PATCH"}):
        assert classified.ingest(**(base | changes)).silver_manifest_id is None
    result = classified.ingest(**(base | {"raw_schema": {"unknown": "string"}}))
    assert result.reason_code == "SCHEMA_VALIDATION_FAILED"
    assert result.bronze_manifest_id and result.silver_manifest_id is None
    assert classified.ingest(**(base | {"code_revision": "working-tree"})).reason_code == "INVALID_CODE_REVISION"


def test_endpoint_method_and_query_are_bound_to_request_hash(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    adapter = ProviderDatasetAdapter(store)
    first = store.read(adapter.ingest(**kwargs()).bronze_manifest_id)[0]
    endpoint = store.read(adapter.ingest(**(kwargs() | {"endpoint": "/api/v1/stocks"})).bronze_manifest_id)[0]
    method = store.read(adapter.ingest(**(kwargs() | {"http_method": "POST"})).bronze_manifest_id)[0]
    query = store.read(adapter.ingest(**(kwargs() | {"request": {"symbol": "B"}})).bronze_manifest_id)[0]
    assert len({first.request_hash, endpoint.request_hash, method.request_hash, query.request_hash}) == 4


def test_secret_pattern_is_rejected_even_when_not_registered(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    params = kwargs() | {"body": {"symbol": "A", "price": "sk-abcdefghijklmnop"}}
    result = ProviderDatasetAdapter(store).ingest(**params)
    assert result.status == "NO_TRADE" and result.bronze_manifest_id is None
    assert "sk-abcdefghijklmnop" not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))


def test_row_count_reads_provider_envelope_and_rejects_ambiguity(tmp_path):
    from asset_management.data.immutable import dataset_row_count
    assert dataset_row_count({"result": [{"id": 1}, {"id": 2}]}) == 2
    assert dataset_row_count({"result": {"items": [{"id": 1}]}}) == 1
    with pytest.raises(ValueError, match="AMBIGUOUS_ROW_COUNT"):
        dataset_row_count({"items": [], "orders": []})


def test_toss_adapter_connects_verified_raw_response_to_dataset_lineage(tmp_path):
    class Client(TossReadOnlyAdapter):
        def __init__(self):
            super().__init__(TossCredentials(
                client_id="client-id", client_secret="client-secret",
                account_seq="account-seq", base_url="https://example.invalid",
                api_env="production",
            ))

        def get_prices(self, symbols):
            return TossApiResult("/api/v1/prices?symbols=A", 200,
                                 {"result": [{"symbol": symbols[0], "price": "10.25"}]}, "raw-1")

        def get_empty(self):
            return TossApiResult("/api/v1/prices", 200, {"result": []}, "")

    store = ImmutableDatasetStore(
        tmp_path,
        secrets=("secret-password", "client-id", "client-secret", "account-seq"),
        credentials_classified=True,
    )
    client = Client()
    adapter = TossDatasetAdapter(client, ProviderDatasetAdapter(store))
    assert adapter.client is client
    result = adapter.collect(
        operation="get_prices", operation_args=(["A"],),
        dataset="prices", retrieved_at=NOW, available_at=NOW,
        provider_timestamp=NOW, license_tag=kwargs()["license_tag"],
        code_revision="git:abc1234", schema_version="prices-v1",
        raw_schema={"result": "array"}, schema=kwargs()["schema"],
        instrument_mapping={"A": "instrument-A"},
        normalize=lambda raw: [{"provider_instrument_id": item["symbol"], "price": item["price"]}
                               for item in raw["result"]],
    )
    bronze, _ = store.read(result.bronze_manifest_id)
    assert result.status == "READY" and bronze.row_count == 1
    with pytest.raises(ValueError, match="RAW_EVIDENCE_MISSING"):
        adapter.collect(
            operation="get_empty",
            dataset="prices", retrieved_at=NOW, available_at=NOW,
            provider_timestamp=NOW, license_tag=kwargs()["license_tag"],
            code_revision="git:abc1234", schema_version="prices-v1",
            raw_schema={"result": "array"}, schema=kwargs()["schema"],
            instrument_mapping={"A": "instrument-A"}, normalize=normalize,
        )
    incomplete = ImmutableDatasetStore(tmp_path / "missing", secrets=("client-secret",),
                                       credentials_classified=True)
    with pytest.raises(ValueError, match="CREDENTIALS_NOT_REGISTERED"):
        TossDatasetAdapter(Client(), ProviderDatasetAdapter(incomplete))
