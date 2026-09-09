from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import DataQualityError
from asset_management.pricing import materialize_usd_fred_risk_free_curve


NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
LICENSE = "purpose=research;redistribution=forbidden;retention=project"


def publish(store: ImmutableDatasetStore, rows: list[dict[str, str]]) -> str:
    return store.write(
        {"observations": rows}, layer="bronze", source="fred-alfred", dataset="risk-free-curve",
        schema_version="fred-risk-free-curve@1", retrieved_at=NOW, available_at=NOW,
        provider_timestamp=NOW, license_tag=LICENSE, code_revision="fred-risk-free@1",
        request_hash="c" * 64, quality_status="RAW").manifest_id


def rows() -> list[dict[str, str]]:
    return [{"series_id": series, "as_of": NOW.isoformat(), "available_at": NOW.isoformat(), "value_percent": "4.00"}
            for series in ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1")]


def test_materializes_exact_usd_curve_from_verified_fred_bundle(tmp_path: Path):
    store = ImmutableDatasetStore(tmp_path)
    manifest_id = publish(store, rows())
    curve = materialize_usd_fred_risk_free_curve(store=store, manifest_id=manifest_id, information_cutoff=NOW)
    assert curve.return_for(currency="USD", horizon=252, information_cutoff=NOW).annualized_rate == Decimal(".04")
    assert {point.dataset_manifest_id for point in curve.points.values()} == {manifest_id}


def test_rejects_missing_tenor_and_any_post_cutoff_observation(tmp_path: Path):
    store = ImmutableDatasetStore(tmp_path)
    with pytest.raises(DataQualityError, match="TENORS_INCOMPLETE"):
        materialize_usd_fred_risk_free_curve(store=store, manifest_id=publish(store, rows()[:-1]), information_cutoff=NOW)
    stale = rows()
    stale[-1]["available_at"] = "2026-09-10T00:00:00+00:00"
    future_id = publish(store, stale)
    with pytest.raises(DataQualityError, match="POINT_NOT_ELIGIBLE"):
        materialize_usd_fred_risk_free_curve(store=store, manifest_id=future_id, information_cutoff=NOW)
