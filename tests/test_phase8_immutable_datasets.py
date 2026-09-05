from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, ProviderDatasetAdapter


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
                source="provider", dataset="prices", retrieved_at=NOW,
                available_at=NOW, provider_timestamp=NOW - timedelta(seconds=1),
                license_tag="internal-use-only;no-redistribution", code_revision="git:abc123",
                schema_version="prices-v1", schema={"provider_instrument_id": "string", "price": "string"},
                instrument_mapping={"A": "instrument-A"}, normalize=normalize)


def test_raw_first_lineage_utc_secrets_and_dedup(tmp_path):
    store = ImmutableDatasetStore(tmp_path, secrets=("secret-password",))
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
    assert silver.row_count == 1 and silver.license_tag.startswith("internal-use-only")
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
    store = ImmutableDatasetStore(tmp_path, secrets=("secret-password",))
    result = ProviderDatasetAdapter(store).ingest(**(kwargs() | changes))
    assert result.status == "NO_TRADE" and result.reason_code == reason
    assert store.read(result.bronze_manifest_id)[1]["symbol"] == "A"
    assert result.silver_manifest_id is None
    health = [json.loads(p.read_bytes()) for p in (tmp_path / "catalog" / "source-health").glob("*.json")]
    assert health[0]["status"] == "BLOCKED" and health[0]["reason_code"] == reason
    assert "secret-password" not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))


def test_revisions_preserve_all_history(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
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
    store = ImmutableDatasetStore(tmp_path)
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
        return ProviderDatasetAdapter(ImmutableDatasetStore(tmp_path)).ingest(**kwargs())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(ingest, range(12)))
    assert all(r == results[0] and r.status == "READY" for r in results)
    assert ImmutableDatasetStore(tmp_path).read(results[0].silver_manifest_id)[1]


@pytest.mark.parametrize("changes", [
    {"license_tag": ""}, {"code_revision": ""},
    {"retrieved_at": NOW.replace(tzinfo=None)},
    {"provider_timestamp": NOW + timedelta(days=1)},
    {"available_at": NOW - timedelta(days=1)},
])
def test_unknown_metadata_fails_closed(tmp_path, changes):
    result = ProviderDatasetAdapter(ImmutableDatasetStore(tmp_path)).ingest(**(kwargs() | changes))
    assert result.status == "NO_TRADE" and result.silver_manifest_id is None


def test_manifest_tamper_and_path_escape(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
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
    store = ImmutableDatasetStore(tmp_path)
    result = ProviderDatasetAdapter(store).ingest(**kwargs())
    silver, _ = store.read(result.silver_manifest_id)
    params = dict(layer="gold", source=silver.source, dataset="features", schema_version="features-v1",
                  retrieved_at=NOW, available_at=NOW, provider_timestamp=NOW,
                  license_tag=silver.license_tag, code_revision="git:abc123",
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
    store = ImmutableDatasetStore(tmp_path)
    result = ProviderDatasetAdapter(store).ingest(**kwargs())
    schema = json.loads((Path(__file__).parents[1] / "schemas/dataset_manifest.schema.json").read_bytes())
    assert set(schema["required"]) == set(asdict(store.read(result.silver_manifest_id)[0]))


def test_catalog_paths_reject_parent_leaf(tmp_path):
    from asset_management.data.layout import DataLakeLayout
    with pytest.raises(ValueError):
        DataLakeLayout(tmp_path).resolve("catalog", "..")
