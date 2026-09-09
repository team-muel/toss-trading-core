"""Canonical raw/silver macro mechanism tests; all market values are synthetic."""
from dataclasses import replace
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal

import pytest
from asset_management.data.alfred import DATASET, SCHEMA_VERSION, SOURCE, ingest_alfred, normalize_vintages
from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.data.phase9 import ProviderBatch
from asset_management.domain.errors import DataQualityError, TemporalViolation
from asset_management.features.macro_state import MacroStateSpec, run_macro_state
from asset_management.time.asof import AsOfContext

NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
RECEIVED = NOW - timedelta(hours=1)
LICENSE = "purpose=synthetic-research;redistribution=forbidden;retention=perpetual"


def row(period, value, start=None, end="9999-12-31"):
    return dict(date=period, realtime_start=start or period, realtime_end=end, value=str(value))


def body(rows):
    return {"count": len(rows), "offset": 0, "observations": rows}


def batch(series, rows, **overrides):
    values = dict(source=SOURCE, dataset=DATASET, endpoint="/fred/series/observations",
        http_method="GET", request={"series_id": series, "output_type": 1, "units": "lin"},
        status_code=200, body=body(rows), provider_timestamp=RECEIVED - timedelta(minutes=1),
        received_at=RECEIVED, available_at=RECEIVED, source_revision="synthetic-v1",
        schema_version=SCHEMA_VERSION, license_tag=LICENSE, code_revision="git:abcdef0")
    values.update(overrides)
    return ProviderBatch(**values)


def context(spec, when=NOW):
    return AsOfContext("macro-fixture", when, when, spec.policy_version, spec.spec_hash, "git:abcdef0")


def fixture_rows():
    months = [date(2025 + i // 12, 1 + i % 12, 1).isoformat() for i in range(20)]
    return {
        "DGS2": [row("2026-09-08", 4)], "DGS10": [row("2026-09-08", 4.5)],
        "CPIAUCSL": [row(day, Decimal(100) + Decimal(i) / 10) for i, day in enumerate(months)],
        "UNRATE": [row(day, Decimal(5) - Decimal(i) / 100) for i, day in enumerate(months)],
        "FEDFUNDS": [row(day, Decimal(5) - Decimal(i) / 100) for i, day in enumerate(months)],
    }


def inputs(tmp_path, rows=None):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    manifests = {}
    for series, observations in (rows or fixture_rows()).items():
        result = ingest_alfred(batch(series, observations), store)
        assert result.status == "READY", result.reason_code
        manifests[series] = result.silver_manifest_id
    return store, manifests


def test_original_macro_hypothesis_matches_canonical_state_without_allocations(tmp_path):
    from research_platform.macro import PointInTimeMacroStore, parse_alfred_payload
    rows = fixture_rows()
    old = PointInTimeMacroStore([obs for series, values in rows.items()
        for obs in parse_alfred_payload(body(values), series_id=series)], publication_lag_days=1)
    spec = MacroStateSpec()
    store, manifests = inputs(tmp_path, rows)
    run = run_macro_state(spec, store=store, manifests=manifests, context=context(spec))
    assert run.payload()["status"] == "COMPUTED"
    assert run.payload()["states"] == old.regime_signals("2026-09-09", lookback_months=3)
    assert run.payload()["semantic_type"] == "MACRO_STATE"
    assert run.payload()["validation_scope"] == "MECHANISM_ONLY"
    assert not hasattr(run, "weights") and not hasattr(run, "orders")
    assert run.evidence_hash == run_macro_state(spec, store=store, manifests=manifests, context=context(spec)).evidence_hash


def test_vintage_bounds_are_inclusive_and_missing_revision_is_not_old_value():
    values = [row("2026-08-01", "100", "2026-08-02", "2026-09-08"), row("2026-08-01", ".", "2026-09-09")]
    normalized = normalize_vintages(body(values), series_id="CPIAUCSL", output_type=1)
    assert normalized[0]["realtime_end"] == "2026-09-08"
    assert normalized[1]["value_status"] == "MISSING"


def test_output3_reconstruction_keeps_revision_tombstones():
    payload = body([{"date": "2026-08-01", "CPIAUCSL_20260802": "100", "CPIAUCSL_20260909": "."}])
    normalized = normalize_vintages(payload, series_id="CPIAUCSL", output_type=3)
    assert normalized[0]["realtime_end"] == "2026-09-08"
    assert normalized[1]["value_status"] == "MISSING"


@pytest.mark.parametrize("modify", [
    lambda x: x.update(count=200), lambda x: x.update(offset=1),
    lambda x: x["observations"].append(row("2026-08-01", 101, "2026-08-02")),
    lambda x: x["observations"][0].update(value=True),
    lambda x: x["observations"][0].update(value="NaN"),
    lambda x: x["observations"][0].update(realtime_end="2026-07-01"),
    lambda x: x["observations"].__setitem__(0, "malformed"),
])
def test_malformed_or_incomplete_response_retains_raw_but_no_silver(tmp_path, modify):
    payload = body([row("2026-08-01", 100, "2026-08-02")]); modify(payload)
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    result = ingest_alfred(batch("CPIAUCSL", [], body=payload), store)
    assert result.status == "NO_TRADE" and result.silver_manifest_id is None
    assert result.bronze_manifest_id is not None


def test_conflicting_or_overlapping_vintages_rejected():
    for values in (
        [row("2026-08-01", 100), row("2026-08-01", 101)],
        [row("2026-08-01", 100, "2026-08-01", "2026-08-03"), row("2026-08-01", 101, "2026-08-03")],
    ):
        with pytest.raises(DataQualityError): normalize_vintages(body(values), series_id="CPIAUCSL", output_type=1)


@pytest.mark.parametrize("changes", [{"lookback_months": True}, {"publication_lag_days": 0},
    {"maximum_rate_age_days": False}, {"source_timezone": "UTC"}, {"policy_version": ""}])
def test_macro_policy_is_explicit_and_strict(changes):
    with pytest.raises(ValueError): MacroStateSpec(**changes)


def test_incomplete_monthly_history_does_not_treat_rows_as_months(tmp_path):
    rows = fixture_rows(); rows["CPIAUCSL"].pop(-5)
    spec = MacroStateSpec(); store, manifests = inputs(tmp_path, rows)
    result = run_macro_state(spec, store=store, manifests=manifests, context=context(spec)).payload()
    assert result["states"] is None and "MISSING_CONTIGUOUS_MONTHLY_HISTORY" in result["reason_codes"]


def test_unknown_missing_stale_and_expired_vintage_do_not_create_scores(tmp_path):
    spec = MacroStateSpec(); rows = fixture_rows()
    rows["DGS2"] = [row("2026-08-01", 4, end="2026-08-31")]; rows["DGS10"] = [row("2026-08-01", 4.5)]
    store, manifests = inputs(tmp_path, rows); manifests.pop("UNRATE")
    result = run_macro_state(spec, store=store, manifests=manifests, context=context(spec)).payload()
    assert result["states"] is None and result["status"] == "UNAVAILABLE"
    assert {"MISSING_SERIES:UNRATE", "MISSING_VINTAGE:DGS2", "STALE_SERIES:DGS10"} <= set(result["reason_codes"])


def test_revision_remains_hidden_until_conservative_local_date_lag(tmp_path):
    spec = MacroStateSpec(); rows = fixture_rows()
    rows["DGS10"][0]["realtime_end"] = "2026-09-08"; rows["DGS10"].append(row("2026-09-08", "3", "2026-09-09"))
    store, manifests = inputs(tmp_path, rows)
    before = run_macro_state(spec, store=store, manifests=manifests, context=context(spec)).payload()
    after = run_macro_state(spec, store=store, manifests=manifests, context=context(spec, NOW + timedelta(days=1))).payload()
    assert before["states"]["yield_curve"] == 1 and after["states"]["yield_curve"] == -1
    assert before["information_set_hash"] != after["information_set_hash"]


def test_repeated_daily_state_does_not_fabricate_new_monthly_observations(tmp_path):
    spec = MacroStateSpec(); store, manifests = inputs(tmp_path)
    a = run_macro_state(spec, store=store, manifests=manifests, context=context(spec)).payload()
    b = run_macro_state(spec, store=store, manifests=manifests, context=context(spec, NOW + timedelta(days=1))).payload()
    assert a["information_set_hash"] == b["information_set_hash"] and a["distinct_periods"] == b["distinct_periods"]


def test_recently_downloaded_archive_cannot_pretend_to_have_been_ingested_in_the_past(tmp_path):
    spec = MacroStateSpec(); store, manifests = inputs(tmp_path)
    with pytest.raises(TemporalViolation):
        run_macro_state(spec, store=store, manifests=manifests, context=context(spec, NOW - timedelta(days=3)))


def test_source_integrity_and_spec_substitution_are_not_ignored(tmp_path):
    spec = MacroStateSpec(); store, manifests = inputs(tmp_path)
    with pytest.raises(DataQualityError, match="CONTEXT_MISMATCH"):
        run_macro_state(replace(spec, lookback_months=6), store=store, manifests=manifests, context=context(spec))
    manifest, _ = store.read(manifests["DGS2"])
    store.layout.resolve("silver", f"{manifest.content_sha256}.json").write_text("[]")
    with pytest.raises(ValueError): run_macro_state(spec, store=store, manifests=manifests, context=context(spec))


def test_silver_value_must_recalculate_from_its_raw_parent(tmp_path):
    spec = MacroStateSpec(); store, manifests = inputs(tmp_path)
    old, rows = store.read(manifests["DGS2"]); rows[0]["value"] = "99"
    forged = store.write(rows, layer="silver", source=old.source, dataset=old.dataset,
        schema_version=old.schema_version, retrieved_at=RECEIVED, available_at=RECEIVED,
        provider_timestamp=RECEIVED, license_tag=LICENSE, code_revision="git:abcdef0",
        request_hash=digest(canonical("forged")), parent_manifest_ids=old.parent_manifest_ids)
    manifests["DGS2"] = forged.manifest_id
    with pytest.raises(DataQualityError, match="NORMALIZATION_MISMATCH"):
        run_macro_state(spec, store=store, manifests=manifests, context=context(spec))


def test_expired_latest_month_is_not_silently_replaced_by_a_recent_older_month(tmp_path):
    spec = MacroStateSpec(); rows = fixture_rows(); rows["UNRATE"][-1]["realtime_end"] = "2026-09-01"
    store, manifests = inputs(tmp_path, rows)
    payload = run_macro_state(spec, store=store, manifests=manifests, context=context(spec)).payload()
    assert payload["states"] is None and "EXPIRED_LATEST_VINTAGE:UNRATE" in payload["reason_codes"]


def test_local_day_policy_does_not_promote_an_unreleased_us_date_at_utc_midnight(tmp_path):
    spec = MacroStateSpec(); rows = fixture_rows()
    rows["DGS10"][0]["realtime_end"] = "2026-09-08"; rows["DGS10"].append(row("2026-09-08", "3", "2026-09-09"))
    store, manifests = inputs(tmp_path, rows)
    instant = datetime(2026, 9, 10, 1, tzinfo=timezone.utc)
    payload = run_macro_state(spec, store=store, manifests=manifests, context=context(spec, instant)).payload()
    assert payload["vintage_cutoff_date"] == "2026-09-08" and payload["states"]["yield_curve"] == 1


def test_missing_latest_vintage_does_not_fall_back_to_previous_value(tmp_path):
    spec = MacroStateSpec(); rows = fixture_rows()
    rows["DGS10"] = [row("2026-09-07", "4.5", end="2026-09-07"), row("2026-09-08", ".", "2026-09-08")]
    store, manifests = inputs(tmp_path, rows)
    payload = run_macro_state(spec, store=store, manifests=manifests, context=context(spec)).payload()
    assert "MISSING_VALUE:DGS10" in payload["reason_codes"] and payload["states"] is None
