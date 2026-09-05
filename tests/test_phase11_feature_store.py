from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.features import company, market
from asset_management.features.leakage import DatedValue, cross_sectional_winsorize, historical_zscore
from asset_management.features.models import FeatureContext, FeatureDefinition, FeatureInput
from asset_management.features.registry import FeatureRegistry, builtin_definitions
from asset_management.features.store import FeatureStore
from asset_management.quality.models import QualityGate, QualityStatus


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
LICENSE = "purpose=internal-research;redistribution=forbidden;retention=perpetual"
VALIDITY = SignalValidity(21, 21, NOW + timedelta(days=1), DecayProfile.LINEAR)


def allow_gate():
    return QualityGate("ALLOW", (), QualityStatus.VALID, QualityStatus.VALID, "HIGH")


def definition(fid="market.return_1m", transform="period_return"):
    return FeatureDefinition(fid, "market", fid.rsplit(".", 1)[-1], "1", ("adjusted_close",),
                             "1m", "current", transform)


def manifests(store):
    common = dict(source="tiingo", retrieved_at=NOW - timedelta(minutes=2),
                  available_at=NOW - timedelta(minutes=1),
                  provider_timestamp=NOW - timedelta(minutes=3), license_tag=LICENSE,
                  code_revision="git:abcdef0", request_hash="a" * 64)
    raw_prices = store.write({"result": [{"ticker": "SPY"}]}, layer="bronze", dataset="prices-raw",
                             schema_version="raw-v1", quality_status="RAW", **common)
    prices = store.write([{"instrument_id": "SPY", "close": "102"}], layer="silver",
                         dataset="prices", schema_version="prices-v1",
                         parent_manifest_ids=(raw_prices.manifest_id,), **common)
    raw_universe = store.write({"result": [{"ticker": "SPY"}]}, layer="bronze",
                               dataset="universe-raw", schema_version="raw-v1",
                               quality_status="RAW", **common)
    universe = store.write([{"instrument_id": "SPY", "active": True}], layer="silver",
                           dataset="historical-universe", schema_version="universe-v1",
                           parent_manifest_ids=(raw_universe.manifest_id,), **common)
    return prices, universe


def context(prices, universe):
    return FeatureContext("SPY", NOW, NOW - timedelta(seconds=30),
                          (prices.manifest_id, universe.manifest_id), universe.manifest_id,
                          "parameters-v1", "state-1", "git:abcdef0", VALIDITY)


def input_value(value, *, available=NOW - timedelta(minutes=1), event=NOW - timedelta(days=1)):
    return FeatureInput({"value": value}, available, event)


def test_builtin_registry_contains_every_required_market_and_company_feature():
    definitions = builtin_definitions()
    assert len(definitions) == 27
    ids = {item.feature_id for item in definitions}
    assert {"market.return_1m", "market.return_3m", "market.return_6m", "market.return_12m",
            "market.momentum_12_1", "market.volatility_20d", "market.volatility_60d",
            "market.drawdown", "market.moving_average_distance", "market.volume_trend",
            "market.breadth", "market.gold_slope", "market.credit_spread"} <= ids
    assert {"company.revenue_growth", "company.eps_growth", "company.fcf_growth",
            "company.fcf_margin", "company.roic", "company.debt_ratio",
            "company.revenue_growth_vs_sector", "company.earnings_growth_vs_sector",
            "company.accrual_ratio", "company.cash_conversion", "company.sbc_to_sales",
            "company.estimate_revision", "company.valuation_multiple",
            "company.shareholder_yield"} <= ids
    assert all(item.missing_policy == "MISSING_HISTORY" and item.quality_policy == "REQUIRE_VALID"
               for item in definitions)


def test_registry_rejects_same_id_with_changed_contract():
    registry = FeatureRegistry([definition()])
    registry.register(definition())
    with pytest.raises(ValueError, match="FEATURE_DEFINITION_CONFLICT"):
        registry.register(definition(transform="moving_average_distance"))
    with pytest.raises(ValueError, match="FEATURE_DEFINITION_INVALID"):
        FeatureDefinition("other.bad", "market", "bad", "1", ("x",), "1d", "current", "ratio")


def test_context_requires_unique_complete_universe_lineage():
    with pytest.raises(ValueError, match="FEATURE_CONTEXT_LINEAGE_INVALID"):
        FeatureContext("SPY", NOW, NOW, ("a", "a"), "a", "parameters-v1", None, "git:abcdef0", VALIDITY)
    with pytest.raises(ValueError, match="FEATURE_CONTEXT_LINEAGE_INVALID"):
        FeatureContext("SPY", NOW, NOW, ("a",), "universe", "parameters-v1", None, "git:abcdef0", VALIDITY)


def test_market_feature_calculations_and_missing_history():
    prices = [Decimal(index) for index in range(100, 113)]
    assert market.period_return(prices, 1) == Decimal("112") / Decimal("111") - 1
    assert market.momentum_12_1(prices) == Decimal("111") / Decimal("100") - 1
    assert market.realized_volatility([100, 101, 99, 102], 3) > 0
    assert market.drawdown_from_high([100, 110, 99]) == Decimal("-0.1")
    assert market.moving_average_distance([90, 100, 110], 3) == Decimal("0.1")
    assert market.volume_trend([1] * 40 + [2] * 20) > 0
    assert market.market_breadth({"A": [9, 10], "B": [11, 10]}, window=2) == Decimal("0.5")
    assert market.trend_slope([100, 110, 121], 3) == Decimal("0.105")
    assert market.credit_spread("5.2", "4.1") == Decimal("1.1")
    with pytest.raises(DataQualityError, match="MISSING_HISTORY"):
        market.period_return([100], 1)


def test_company_feature_calculations_cover_required_contract():
    assert company.growth(120, 100) == Decimal("0.2")
    assert company.fcf_margin(20, 100) == Decimal("0.2")
    assert company.roic(15, 100) == Decimal("0.15")
    assert company.debt_ratio(30, 100) == Decimal("0.3")
    assert company.relative_growth("0.2", "0.1") == Decimal("0.1")
    assert company.accrual_ratio(20, 15, 100) == Decimal("0.05")
    assert company.cash_conversion(24, 20) == Decimal("1.2")
    assert company.stock_compensation_to_sales(3, 100) == Decimal("0.03")
    assert company.estimate_revision(105, 100) == Decimal("0.05")
    assert company.valuation_multiple(1000, 100) == Decimal("10")
    assert company.shareholder_yield(10, 20, 5, 500) == Decimal("0.05")
    with pytest.raises(DataQualityError, match="FEATURE_DENOMINATOR_INVALID"):
        company.growth(1, 0)


def test_historical_standardization_excludes_current_and_future_information():
    history = [
        DatedValue(Decimal("1"), NOW - timedelta(days=3), NOW - timedelta(days=3)),
        DatedValue(Decimal("2"), NOW - timedelta(days=2), NOW - timedelta(days=2)),
        DatedValue(Decimal("100"), NOW + timedelta(days=1), NOW + timedelta(days=1)),
    ]
    result = historical_zscore(3, history, as_of=NOW,
                               information_cutoff=NOW - timedelta(seconds=1), minimum_history=2)
    assert result > 0
    with pytest.raises(DataQualityError, match="MISSING_HISTORY"):
        historical_zscore(3, history, as_of=NOW,
                          information_cutoff=NOW - timedelta(days=2, seconds=1), minimum_history=2)


def test_winsorization_uses_exact_historical_cross_section():
    result = cross_sectional_winsorize({"A": 1, "B": 2, "C": 100},
                                       historical_universe=("A", "B", "C"),
                                       lower_fraction=Decimal("0.34"), upper_fraction=Decimal("0.66"))
    assert result == {"A": Decimal("1"), "B": Decimal("2"), "C": Decimal("2")}
    with pytest.raises(DataQualityError, match="HISTORICAL_UNIVERSE_MISMATCH"):
        cross_sectional_winsorize({"A": 1, "B": 2}, historical_universe=("A",))


def test_same_inputs_and_parameters_produce_identical_feature_and_lineage(tmp_path):
    lake = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    prices, universe = manifests(lake)
    registry = FeatureRegistry([definition()])
    store = FeatureStore(lake, registry)
    args = dict(feature_id="market.return_1m", context=context(prices, universe),
                inputs={"adjusted_close": input_value([Decimal("100"), Decimal("102")])},
                transform=market.period_return, parameters={"periods": 1}, quality_gate=allow_gate())
    first = store.evaluate(**args)
    second = store.evaluate(**args)
    assert first.status == "READY" and first.snapshot == second.snapshot
    assert first.manifest_id == second.manifest_id
    manifest, body = lake.read(first.manifest_id)
    assert set(manifest.parent_manifest_ids) == {prices.manifest_id, universe.manifest_id}
    assert body["input_manifest_ids"] == sorted([prices.manifest_id, universe.manifest_id])
    assert body["parameter_set_id"] == "parameters-v1"
    assert body["parent_state_id"] == "state-1" and body["code_revision"] == "git:abcdef0"
    assert body["validity"] == VALIDITY.payload()
    assert manifest.retrieved_at == NOW.isoformat() and manifest.available_at == NOW.isoformat()


def test_future_quarter_and_missing_history_fail_closed_without_gold(tmp_path):
    lake = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    prices, universe = manifests(lake)
    store = FeatureStore(lake, FeatureRegistry([definition()]))
    base = dict(feature_id="market.return_1m", context=context(prices, universe),
                transform=market.period_return, parameters={"periods": 1}, quality_gate=allow_gate())
    future = store.evaluate(**base, inputs={"adjusted_close": input_value(
        [100, 102], available=NOW + timedelta(days=1), event=NOW + timedelta(days=1))})
    assert future.status == "NO_TRADE" and future.reason_code == "FEATURE_INPUT_AFTER_CUTOFF"
    assert future.manifest_id is None and future.snapshot.value is None
    missing = store.evaluate(**base, inputs={})
    assert missing.status == "NO_TRADE" and missing.reason_code == "MISSING_HISTORY"
    assert missing.snapshot.value is None


def test_quality_and_transform_mismatch_cannot_become_valid(tmp_path):
    lake = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    prices, universe = manifests(lake)
    store = FeatureStore(lake, FeatureRegistry([definition()]))
    ctx = context(prices, universe)
    blocked = QualityGate("NO_TRADE", ("SOURCE_STALE",), QualityStatus.STALE,
                          QualityStatus.STALE, "BLOCKED")
    result = store.evaluate(feature_id="market.return_1m", context=ctx,
                            inputs={"adjusted_close": input_value([100, 102])},
                            transform=market.period_return, parameters={"periods": 1},
                            quality_gate=blocked)
    assert result.status == "NO_TRADE" and result.reason_code == "SOURCE_STALE"
    mismatch = store.evaluate(feature_id="market.return_1m", context=ctx,
                              inputs={"adjusted_close": input_value([100, 102])},
                              transform=market.moving_average_distance, parameters={"window": 2},
                              quality_gate=allow_gate())
    assert mismatch.reason_code == "FEATURE_TRANSFORMATION_MISMATCH"


def test_quality_gate_is_part_of_deterministic_run_identity(tmp_path):
    lake = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    prices, universe = manifests(lake)
    store = FeatureStore(lake, FeatureRegistry([definition()]))
    common = dict(feature_id="market.return_1m", context=context(prices, universe),
                  inputs={"adjusted_close": input_value([100, 102])}, transform=market.period_return,
                  parameters={"periods": 1})
    ready = store.evaluate(**common, quality_gate=allow_gate())
    blocked = store.evaluate(**common, quality_gate=QualityGate(
        "NO_TRADE", ("SOURCE_STALE",), QualityStatus.STALE, QualityStatus.STALE, "BLOCKED"))
    assert ready.snapshot.feature_run_id != blocked.snapshot.feature_run_id


def test_feature_is_numeric_state_and_never_buy_or_sell():
    prohibited = {"BUY", "SELL"}
    assert all(item.transformation not in prohibited and item.name not in prohibited
               for item in builtin_definitions())
