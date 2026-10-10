"""Synthetic mechanisms only; no yield curve or OOS acceptance evidence."""
import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from asset_management.data.alfred import DATASET, SCHEMA_VERSION, SERIES, SOURCE, ingest_alfred, normalize_vintages
from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.data.phase9 import ProviderBatch
from asset_management.domain.errors import DataQualityError
from asset_management.pricing.fred_risk_free import FRED_USD_RISK_FREE_SERIES


@pytest.mark.parametrize("series", FRED_USD_RISK_FREE_SERIES.values())
@pytest.mark.parametrize("output_type", [1, 3])
def test_tenor_observations_preserve_raw_vintages_and_lineage(tmp_path, series, output_type):
    observation = ({"date": "2026-10-08", "realtime_start": "2026-10-09",
                    "realtime_end": "9999-12-31", "value": "4.1230"}
                   if output_type == 1 else {"date": "2026-10-08", f"{series}_20261009": "4.1230"})
    body = {"count": 1, "offset": 0, "observations": [observation]}
    received = datetime(2026, 10, 11, tzinfo=timezone.utc)
    captured = ProviderBatch(source=SOURCE, dataset=DATASET, schema_version=SCHEMA_VERSION,
        endpoint="/fred/series/observations", http_method="GET", status_code=200,
        request={"series_id": series, "output_type": output_type, "units": "lin"},
        body=body, received_at=received, available_at=received, provider_timestamp=received,
        source_revision="synthetic", code_revision="git:abcdef0",
        license_tag="purpose=synthetic-research;redistribution=forbidden;retention=perpetual")
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    result = ingest_alfred(captured, store)
    assert result.status == "READY"
    assert ingest_alfred(captured, store) == result
    manifest, rows = store.read(result.silver_manifest_id)
    assert manifest.parent_manifest_ids == (result.bronze_manifest_id,)
    assert store.read(result.bronze_manifest_id)[1] == body
    assert rows[0]["series_id"] == series and rows[0]["value"] == "4.1230"
    assert rows[0]["realtime_start"] == "2026-10-09"
    assert manifest.available_at == received.isoformat()
    assert "annualized_rate" not in rows[0] and "holding_period_risk_free_return" not in rows[0]


def test_curve_tenors_have_explicit_approved_source_registry():
    path = Path(__file__).resolve().parents[1] / "config/fred_series.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        registry = {row["series_id"]: row for row in csv.DictReader(stream)}
    for series in FRED_USD_RISK_FREE_SERIES.values():
        row = registry[series]
        assert row["enabled"] == "true" and row["rights_review"] == "approved"
        assert row["source_url"] == f"https://fred.stlouisfed.org/series/{series}"
        assert "investment basis percent" in row["notes"]
    assert SERIES == {"DGS2", "DGS10", "CPIAUCSL", "UNRATE", "FEDFUNDS"}


@pytest.mark.parametrize("series", ["DTB3", "VIXCLS", "UNKNOWN"])
def test_unassigned_or_discount_basis_series_not_silently_admitted(series):
    with pytest.raises(DataQualityError, match="ALFRED_REQUEST_CONTRACT_INVALID"):
        normalize_vintages({"count": 0, "offset": 0, "observations": []},
                           series_id=series, output_type=1)
