from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import ast

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, ProviderDatasetAdapter
from asset_management.data.phase9 import (
    ACTION_FIELDS,
    COLLECTION_ORDER,
    CONSENSUS_FIELDS,
    CollectionPlan,
    CollectionStage,
    FINANCIAL_METRICS,
    INITIAL_DAILY_ETFS,
    LatestSuccessfulDataset,
    MACRO_SERIES,
    Phase9Collector,
    ProviderBatch,
    surprise,
)
from asset_management.domain.errors import DataQualityError


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
LICENSE = "purpose=internal-research;redistribution=forbidden;retention=perpetual"


def environment(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    return store, Phase9Collector(ProviderDatasetAdapter(store))


def batch(dataset, rows, *, status=200, available=NOW, revision="provider-r1", source="provider"):
    return ProviderBatch(
        source=source, dataset=dataset, endpoint=f"/v1/{dataset}", http_method="GET",
        request={"dataset": dataset}, status_code=status, body={"result": rows},
        provider_timestamp=NOW - timedelta(seconds=2), received_at=NOW,
        available_at=available, source_revision=revision, schema_version=f"{dataset}-v1",
        license_tag=LICENSE, code_revision="git:abcdef0",
    )


def base(entity, source="provider"):
    return {"provider_entity_id": entity, "source": source, "source_revision": "provider-r1"}


def price(symbol="SPY"):
    return {
        "provider_instrument_id": symbol, "event_time_utc": STAMP,
        "available_at": STAMP, "exchange_local_date": "2026-09-05",
        "open": "100", "high": "102", "low": "99", "close": "101",
        "volume": "1000000", "currency": "USD", "session": "REGULAR",
        "adjustment": "raw", "source": "provider", "source_revision": "provider-r1",
    }


def test_daily_etf_prices_require_complete_fields_and_canonical_mapping(tmp_path):
    store, collector = environment(tmp_path)
    result = collector.daily_prices(batch("daily-prices", [price()]), {"SPY": "instrument-spy"})
    manifest, rows = store.read(result.silver_manifest_id)
    assert result.status == "READY" and manifest.row_count == 1
    assert rows[0]["instrument_id"] == "instrument-spy"
    assert set({"SPY"}) <= INITIAL_DAILY_ETFS
    missing = price()
    missing.pop("volume")
    failed = collector.daily_prices(batch("daily-prices", [missing]), {"SPY": "instrument-spy"})
    assert failed.status == "NO_TRADE" and failed.reason_code == "VOLUME_MISSING"
    assert failed.bronze_manifest_id and failed.silver_manifest_id is None
    with pytest.raises(DataQualityError, match="INITIAL_ETF_UNIVERSE_INVALID"):
        collector.daily_prices(batch("daily-prices", [price("AAPL")]), {"AAPL": "instrument-aapl"})


def test_calendar_and_corporate_actions_precede_prices_and_known_empty_is_preserved(tmp_path):
    store, collector = environment(tmp_path)
    session = base("XNYS") | {
        "exchange_local_date": "2026-09-05", "is_open": True,
        "regular_open_at": (NOW - timedelta(hours=3)).isoformat(),
        "regular_close_at": (NOW + timedelta(hours=3)).isoformat(),
        "event_time_utc": STAMP, "received_at": STAMP, "available_at": STAMP,
    }
    assert collector.trading_sessions(batch("sessions", [session]), {"XNYS": "exchange-xnys"}).status == "READY"
    empty = collector.corporate_actions(batch("actions", []), {"SPY": "instrument-spy"})
    manifest, rows = store.read(empty.silver_manifest_id)
    assert rows == [] and manifest.row_count == 0 and manifest.quality_status == "VALID"
    action = base("SPY") | {
        "action_type": "SPLIT", "effective_date": "2026-09-05", "terms": {"ratio": "2"},
        "event_time_utc": STAMP, "received_at": STAMP, "available_at": STAMP,
    }
    assert collector.corporate_actions(batch("actions", [action]), {"SPY": "instrument-spy"}).status == "READY"
    assert COLLECTION_ORDER[:3] == (
        CollectionStage.TRADING_SESSIONS, CollectionStage.CORPORATE_ACTIONS,
        CollectionStage.DAILY_PRICES,
    )


def test_price_context_gold_requires_open_session_and_preserves_all_parents(tmp_path):
    store, collector = environment(tmp_path)
    session = base("XNYS") | {
        "exchange_local_date": "2026-09-05", "is_open": True,
        "regular_open_at": (NOW - timedelta(hours=3)).isoformat(),
        "regular_close_at": (NOW + timedelta(hours=3)).isoformat(),
        "event_time_utc": STAMP, "received_at": STAMP, "available_at": STAMP,
    }
    sessions = collector.trading_sessions(batch("sessions", [session]), {"XNYS": "exchange-xnys"})
    actions = collector.corporate_actions(batch("actions", []), {"SPY": "instrument-spy"})
    prices = collector.daily_prices(batch("daily-prices", [price()]), {"SPY": "instrument-spy"})
    gold = collector.attach_price_context(
        price_manifest_id=prices.silver_manifest_id,
        session_manifest_id=sessions.silver_manifest_id,
        action_manifest_id=actions.silver_manifest_id,
        instrument_exchange={"instrument-spy": "exchange-xnys"}, code_revision="git:abcdef0",
    )
    assert gold.layer == "gold"
    assert set(gold.parent_manifest_ids) == {
        prices.silver_manifest_id, sessions.silver_manifest_id, actions.silver_manifest_id,
    }
    with pytest.raises(DataQualityError, match="PRICE_SESSION_MISSING_OR_CLOSED"):
        collector.attach_price_context(
            price_manifest_id=prices.silver_manifest_id,
            session_manifest_id=sessions.silver_manifest_id,
            action_manifest_id=actions.silver_manifest_id,
            instrument_exchange={"instrument-spy": "exchange-wrong"}, code_revision="git:abcdef0",
        )


def test_cross_provider_price_context_requires_non_weakened_combined_license(tmp_path):
    store, collector = environment(tmp_path)
    session = base("XNYS", "calendar-provider") | {
        "exchange_local_date": "2026-09-05", "is_open": True,
        "regular_open_at": (NOW - timedelta(hours=3)).isoformat(),
        "regular_close_at": (NOW + timedelta(hours=3)).isoformat(),
        "event_time_utc": STAMP, "received_at": STAMP, "available_at": STAMP,
    }
    sessions = collector.trading_sessions(
        batch("sessions", [session], source="calendar-provider"), {"XNYS": "exchange-xnys"})
    actions = collector.corporate_actions(batch("actions", []), {"SPY": "instrument-spy"})
    prices = collector.daily_prices(batch("daily-prices", [price()]), {"SPY": "instrument-spy"})
    values = dict(price_manifest_id=prices.silver_manifest_id,
                  session_manifest_id=sessions.silver_manifest_id,
                  action_manifest_id=actions.silver_manifest_id,
                  instrument_exchange={"instrument-spy": "exchange-xnys"},
                  code_revision="git:abcdef0")
    with pytest.raises(DataQualityError, match="COMBINED_CONTRACT_REQUIRED"):
        collector.attach_price_context(**values)
    gold = collector.attach_price_context(
        **values, combined_source="provider+calendar-provider",
        combined_license_tag=LICENSE,
    )
    assert gold.source == "provider+calendar-provider"
    with pytest.raises(ValueError, match="COMBINED_LICENSE_WEAKENS_PARENT"):
        collector.attach_price_context(
            **values, combined_source="provider+calendar-provider",
            combined_license_tag="purpose=internal-research;redistribution=permitted;retention=perpetual",
        )


def test_fx_separates_reporting_and_execution_quotes(tmp_path):
    store, collector = environment(tmp_path)
    quote = base("USD/KRW") | {
        "event_time": STAMP, "available_at": STAMP, "bid": "1330.10",
        "ask": "1330.30", "mid": "1330.20", "quote_type": "REPORTING",
    }
    reporting = collector.fx(batch("fx", [quote]), {"USD/KRW": "fx-usd-krw"})
    execution = collector.fx(batch("fx", [quote | {"quote_type": "EXECUTED_CONVERSION"}]),
                             {"USD/KRW": "fx-usd-krw"})
    assert reporting.silver_manifest_id != execution.silver_manifest_id
    assert store.read(reporting.silver_manifest_id)[1][0]["quote_type"] == "REPORTING"
    failed = collector.fx(batch("fx", [quote | {"mid": "1400"}]), {"USD/KRW": "fx-usd-krw"})
    assert failed.reason_code == "FX_QUOTE_CONFLICT"


def test_risk_free_curve_requires_all_four_horizons(tmp_path):
    _, collector = environment(tmp_path)
    rows = [base("USD-RF") | {"event_time": STAMP, "available_at": STAMP,
            "horizon_months": horizon, "rate": rate}
            for horizon, rate in ((1, "0.040"), (3, "0.041"), (6, "0.042"), (12, "0.043"))]
    assert collector.risk_free_curve(batch("risk-free", rows), {"USD-RF": "curve-usd"}).status == "READY"
    failed = collector.risk_free_curve(batch("risk-free", rows[:-1]), {"USD-RF": "curve-usd"})
    assert failed.reason_code == "RISK_FREE_CURVE_INCOMPLETE"


def macro_rows():
    return [base(series) | {
        "reference_period": "2026-08", "actual": "1.0", "prior_value": "0.9",
        "prior_vintage": "2026-08-01", "revised_prior": "0.95",
        "scheduled_release_at": (NOW - timedelta(minutes=2)).isoformat(),
        "official_release_at": (NOW - timedelta(minutes=1)).isoformat(),
        "received_at": STAMP, "available_at": STAMP,
    } for series in sorted(MACRO_SERIES)]


def test_macro_minimum_set_and_release_times(tmp_path):
    _, collector = environment(tmp_path)
    mapping = {series: f"series-{series.lower()}" for series in MACRO_SERIES}
    assert collector.macro(batch("macro", macro_rows()), mapping).status == "READY"
    incomplete = collector.macro(batch("macro", macro_rows()[:-1]), mapping)
    assert incomplete.reason_code == "MACRO_MINIMUM_SET_INCOMPLETE"
    early = macro_rows()
    early[0]["available_at"] = (NOW - timedelta(minutes=3)).isoformat()
    assert collector.macro(batch("macro", early), mapping).reason_code == "MACRO_AVAILABLE_AT_INVALID"


def test_consensus_surprise_requires_real_pre_release_snapshot(tmp_path):
    _, collector = environment(tmp_path)
    row = base("CPI") | {
        "reference_period": "2026-08", "consensus": "2.1",
        "snapshot_at": (NOW - timedelta(hours=2)).isoformat(),
        "available_at": (NOW - timedelta(hours=2)).isoformat(), "quality_status": "KNOWN",
    }
    assert collector.consensus(batch("consensus", [row]), {"CPI": "series-cpi"}).status == "READY"
    assert surprise(actual="2.4", consensus_row=row,
                    official_release_at=NOW - timedelta(hours=1)) == Decimal("0.3")
    unknown = collector.consensus(batch("consensus", [row | {"quality_status": "UNKNOWN"}]),
                                  {"CPI": "series-cpi"})
    assert unknown.status == "NO_TRADE" and unknown.reason_code == "CONSENSUS_HISTORY_UNKNOWN"
    with pytest.raises(DataQualityError, match="CONSENSUS_NOT_PRE_RELEASE"):
        surprise(actual="2.4", consensus_row=row, official_release_at=NOW - timedelta(hours=3))
    with pytest.raises(DataQualityError, match="OFFICIAL_RELEASE_NOT_TIMEZONE_AWARE"):
        surprise(actual="2.4", consensus_row=row, official_release_at=NOW.replace(tzinfo=None))
    with pytest.raises(DataQualityError, match="SURPRISE_VALUE_INVALID"):
        surprise(actual="NaN", consensus_row=row, official_release_at=NOW - timedelta(hours=1))


def filing(form):
    offset = {"10-K": 4, "10-Q": 3, "8-K": 2, "EARNINGS_RELEASE": 1}[form]
    filed = NOW - timedelta(hours=offset)
    return base("CIK-1") | {
        "form_type": form, "period_end": "2026-06-30", "filed_at": filed.isoformat(),
        "accepted_at": (filed + timedelta(minutes=1)).isoformat(),
        "received_at": (filed + timedelta(minutes=2)).isoformat(),
        "available_at": (filed + timedelta(minutes=2)).isoformat(),
    }


def test_company_filings_enforce_priority_and_all_timestamps(tmp_path):
    store, collector = environment(tmp_path)
    rows = [filing(form) for form in ("10-K", "10-Q", "8-K", "EARNINGS_RELEASE")]
    result = collector.filings(batch("filings", rows), {"CIK-1": "company-1"})
    assert result.status == "READY" and store.read(result.silver_manifest_id)[1][0]["form_type"] == "10-K"
    reversed_result = collector.filings(batch("filings", list(reversed(rows))), {"CIK-1": "company-1"})
    assert reversed_result.reason_code == "FILING_COLLECTION_ORDER_INVALID"
    empty = collector.filings(batch("filings", []), {"CIK-1": "company-1"})
    assert store.read(empty.silver_manifest_id)[0].row_count == 0


def financial_row():
    values = {metric: "1" for metric in FINANCIAL_METRICS}
    values.update({"operating_cash_flow": "10", "capex": "3", "free_cash_flow": "7"})
    return base("CIK-1") | values | {
        "reference_period": "2026-Q2", "period_end": "2026-06-30",
        "filed_at": (NOW - timedelta(minutes=3)).isoformat(),
        "accepted_at": (NOW - timedelta(minutes=2)).isoformat(),
        "received_at": (NOW - timedelta(minutes=1)).isoformat(), "available_at": STAMP,
    }


def test_financial_statements_require_every_metric_and_consistent_fcf(tmp_path):
    _, collector = environment(tmp_path)
    mapping = {"CIK-1": "company-1"}
    assert collector.financials(batch("financials", [financial_row()]), mapping).status == "READY"
    missing = financial_row()
    missing.pop("revenue")
    assert collector.financials(batch("financials", [missing]), mapping).reason_code == "REVENUE_MISSING"
    conflict = financial_row() | {"free_cash_flow": "99"}
    assert collector.financials(batch("financials", [conflict]), mapping).reason_code == "FREE_CASH_FLOW_CONFLICT"


def test_analyst_estimates_require_historical_known_snapshot(tmp_path):
    store, collector = environment(tmp_path)
    row = base("CIK-1") | {
        "metric": "revenue", "forecast_period": "2026-Q3", "consensus": "100",
        "analyst_count": 12, "snapshot_at": (NOW - timedelta(days=1)).isoformat(),
        "available_at": (NOW - timedelta(days=1)).isoformat(), "quality_status": "KNOWN",
    }
    result = collector.analyst_estimates(batch("estimates", [row]), {"CIK-1": "company-1"})
    assert store.read(result.silver_manifest_id)[1][0]["analyst_count"] == 12
    unknown = collector.analyst_estimates(batch("estimates", [row | {"quality_status": "UNKNOWN"}]),
                                          {"CIK-1": "company-1"})
    assert unknown.reason_code == "ESTIMATE_HISTORY_UNKNOWN"


def test_source_revision_conflict_and_missing_values_never_become_zero(tmp_path):
    _, collector = environment(tmp_path)
    wrong = price() | {"source_revision": "other"}
    assert collector.daily_prices(batch("daily-prices", [wrong]), {"SPY": "instrument-spy"}).reason_code == \
        "SOURCE_REVISION_CONFLICT"
    missing = price() | {"close": None}
    result = collector.daily_prices(batch("daily-prices", [missing]), {"SPY": "instrument-spy"})
    assert result.status == "NO_TRADE" and result.reason_code == "CLOSE_MISSING"
    future_available = price() | {"available_at": (NOW + timedelta(minutes=1)).isoformat()}
    result = collector.daily_prices(
        batch("daily-prices", [future_available]), {"SPY": "instrument-spy"})
    assert result.reason_code == "DATASET_AVAILABLE_BEFORE_ROW"


def test_failed_api_never_replaces_latest_success(tmp_path):
    store, collector = environment(tmp_path)
    success = collector.daily_prices(batch("daily-prices", [price()]), {"SPY": "instrument-spy"})
    failure = collector.daily_prices(batch("daily-prices", [price()], status=503,
                                                 available=NOW + timedelta(minutes=1)),
                                     {"SPY": "instrument-spy"})
    latest = LatestSuccessfulDataset(store).get(
        source="provider", dataset="daily-prices", cutoff=NOW + timedelta(minutes=2))
    assert failure.status == "NO_TRADE" and failure.silver_manifest_id is None
    assert latest.manifest_id == success.silver_manifest_id


def test_collection_plan_is_complete_ordered_and_stops_after_failure():
    calls = []
    ready = lambda: type("Result", (), {"status": "READY"})()
    tasks = {stage: (lambda current=stage: (calls.append(current), ready())[1]) for stage in COLLECTION_ORDER}
    results = CollectionPlan().run(tasks)
    assert calls == list(COLLECTION_ORDER) and len(results) == len(COLLECTION_ORDER)
    with pytest.raises(DataQualityError, match="COLLECTION_PLAN_INCOMPLETE"):
        CollectionPlan().run({})
    calls.clear()
    blocked = type("Result", (), {"status": "NO_TRADE"})()
    tasks[CollectionStage.FX] = lambda: (calls.append(CollectionStage.FX), blocked)[1]
    assert len(CollectionPlan().run(tasks)) == 4
    assert calls[-1] is CollectionStage.FX


def test_phase9_data_adapters_cannot_import_or_modify_account_database():
    root = Path(__file__).parents[1] / "src/asset_management/data"
    forbidden = {"sqlite3", "asset_management.account", "asset_management.ledger"}
    violations = []
    for path in (root / "phase9.py",):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else []
            values = ([module] if module else []) + names
            violations.extend(value for value in values if any(value == item or value.startswith(item + ".")
                                                                for item in forbidden))
    assert violations == []
