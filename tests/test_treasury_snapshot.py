"""Synthetic quote mechanisms only; never OOS or pricing acceptance."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from asset_management.data.alfred import DATASET, SCHEMA_VERSION, SOURCE, ingest_alfred
from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.data.phase9 import ProviderBatch
from asset_management.data.treasury_snapshot import TENORS, persist_treasury_quote_snapshot
from asset_management.domain.errors import DataQualityError, TemporalViolation
from asset_management.time.asof import AsOfContext

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
RECEIVED = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
LICENSE = "purpose=synthetic-research;redistribution=forbidden;retention=perpetual"


def fixture(tmp_path, *, missing=False):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    manifests = {}
    for series in TENORS:
        payload = {"count": 1, "offset": 0, "observations": [{"date": "2026-10-08",
            "realtime_start": "2026-10-09", "realtime_end": "9999-12-31",
            "value": "." if missing and series == "DGS3MO" else "4.23"}]}
        captured = ProviderBatch(source=SOURCE, dataset=DATASET, schema_version=SCHEMA_VERSION,
            endpoint="/fred/series/observations", http_method="GET", status_code=200,
            request={"series_id": series, "output_type": 1}, body=payload,
            received_at=RECEIVED, available_at=RECEIVED, provider_timestamp=RECEIVED,
            source_revision="synthetic", code_revision="git:abcdef0", license_tag=LICENSE)
        result = ingest_alfred(captured, store)
        assert result.status == "READY"
        manifests[series] = result.silver_manifest_id
    return store, manifests


def context():
    return AsOfContext("quote-mechanism", NOW, NOW, "research-policy", "test-spec", "git:abcdef0")


def persist(store, manifests, **kwargs):
    values = dict(store=store, manifests=manifests, observation_date="2026-10-08",
                  vintage_date="2026-10-09", context=context())
    values.update(kwargs)
    return persist_treasury_quote_snapshot(**values)


def test_quote_snapshot_replays_source_and_keeps_convention(tmp_path):
    store, manifests = fixture(tmp_path)
    result = persist(store, manifests)
    assert persist(store, manifests) == result
    manifest, body = store.read(result.manifest_id)
    assert manifest.layer == "gold" and manifest.parent_manifest_ids == tuple(sorted(manifests.values()))
    assert body["quote_basis"] == "BOND_EQUIVALENT_SIMPLE_ANNUAL"
    assert body["source_day_count"] == "ACTUAL_365_OR_366" and body["coupon_convention"] == "SEMIANNUAL"
    assert body["official_publication_time"] == "UNKNOWN" and not body["return_authority"]
    assert body["status"] == "OBSERVED" and manifest.available_at == NOW.isoformat()
    assert all(q["observation"]["value"] == "4.23" for q in body["quotes"])
    assert "annualized_rate" not in body and "forecast_horizon" not in body


def test_missing_revision_remains_missing(tmp_path):
    store, manifests = fixture(tmp_path, missing=True)
    _, body = store.read(persist(store, manifests).manifest_id)
    assert body["status"] == "MISSING"
    assert next(q for q in body["quotes"] if q["series_id"] == "DGS3MO")["observation"]["value"] == "."


@pytest.mark.parametrize("change", [
    {"vintage_date": "2026-10-11"}, {"observation_date": "2026-10-12"},
    {"observation_date": "2026-09-01"}, {"vintage_date": "2026-10-08"},
    {"context": None}, {"observation_date": "not-a-date"},
])
def test_ineligible_dates_or_context_do_not_publish(tmp_path, change):
    store, manifests = fixture(tmp_path)
    with pytest.raises((DataQualityError, TemporalViolation)):
        persist(store, manifests, **change)
    assert not list((tmp_path / "gold").glob("*.json"))


def test_future_receipt_and_incomplete_or_wrong_series_fail_closed(tmp_path):
    store, manifests = fixture(tmp_path)
    with pytest.raises(TemporalViolation):
        persist(store, manifests, context=replace(context(), information_cutoff_utc=RECEIVED.replace(hour=11)))
    with pytest.raises(DataQualityError, match="TREASURY_TENORS_INCOMPLETE"):
        persist(store, {k: v for k, v in manifests.items() if k != "DGS1"})
    wrong = dict(manifests, DGS1=manifests["DGS3MO"])
    with pytest.raises(DataQualityError, match="ALFRED_NORMALIZATION_MISMATCH"):
        persist(store, wrong)
    assert not list((tmp_path / "gold").glob("*.json"))


@pytest.mark.parametrize("field,value", [("quote_basis", "EFFECTIVE_ANNUAL"),
    ("return_authority", True), ("return_authority", 0), ("status", "OBSERVED"),
    ("official_publication_time", "2026-10-09T00:00:00Z")])
def test_rehashed_forged_snapshot_is_rejected_on_read(tmp_path, field, value):
    store, manifests = fixture(tmp_path, missing=True)
    good, body = store.read(persist(store, manifests).manifest_id)
    body[field] = value
    forged = store.write(body, layer="gold", source=good.source, dataset=good.dataset,
        schema_version=good.schema_version, retrieved_at=NOW, available_at=NOW,
        provider_timestamp=RECEIVED, license_tag=good.license_tag, code_revision=good.code_revision,
        request_hash=good.request_hash, parent_manifest_ids=good.parent_manifest_ids)
    with pytest.raises(DataQualityError, match="TREASURY_SNAPSHOT_LINEAGE_CONFLICT"):
        store.read(forged.manifest_id)


def test_source_loss_blocks_later_snapshot_read(tmp_path):
    store, manifests = fixture(tmp_path)
    result = persist(store, manifests)
    parent = store.read_manifest(manifests["DGS1"])
    raw = store.read_manifest(parent.parent_manifest_ids[0])
    store.layout.resolve("bronze", f"{raw.content_sha256}.json").unlink()
    with pytest.raises(OSError): store.read(result.manifest_id)


@pytest.mark.parametrize("change", [{"output_type": True}, {"units": "pch"}, {"series_id": "DTB3"}])
def test_rehashed_wrong_raw_request_cannot_authorize_quote(tmp_path, change):
    store, manifests = fixture(tmp_path)
    silver, rows = store.read(manifests["DGS1"])
    bronze, raw = store.read(silver.parent_manifest_ids[0])
    request = store.read_catalog("requests", bronze.request_hash)
    request["request"].update(change)
    request_hash = store.catalog("requests", request)
    bad_raw = store.write(raw, layer="bronze", source=bronze.source, dataset=bronze.dataset,
        schema_version=bronze.schema_version, retrieved_at=RECEIVED, available_at=RECEIVED,
        provider_timestamp=RECEIVED, license_tag=LICENSE, code_revision=bronze.code_revision,
        request_hash=request_hash, quality_status="RAW")
    bad_silver = store.write(rows, layer="silver", source=silver.source, dataset=silver.dataset,
        schema_version=silver.schema_version, retrieved_at=RECEIVED, available_at=RECEIVED,
        provider_timestamp=RECEIVED, license_tag=LICENSE, code_revision=silver.code_revision,
        request_hash=request_hash, parent_manifest_ids=(bad_raw.manifest_id,))
    manifests["DGS1"] = bad_silver.manifest_id
    with pytest.raises(DataQualityError, match="ALFRED_REQUEST_CONFLICT"):
        persist(store, manifests)
    assert not list((tmp_path / "gold").glob("*.json"))
