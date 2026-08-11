from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from toss_trading.research import (
    DataLake,
    MacroRegimeConfig,
    MacroVintageObservation,
    PointInTimeMacroStore,
    PricePoint,
    load_alfred_from_manifests,
    run_macro_regime_backtest,
    parse_alfred_payload,
)
from toss_trading.research.costs import ExecutionCostModel, SlippageTier
from toss_trading.research.candidate_evaluation import evaluate_hypothesis
from toss_trading.research.hypotheses import load_research_policy


def _macro_observations() -> list[MacroVintageObservation]:
    rows: list[MacroVintageObservation] = []
    current = date(2014, 1, 1)
    for index in range(120):
        observation_date = current.isoformat()
        released = (current + timedelta(days=10)).isoformat()
        values = {
            "DGS2": 2.0,
            "DGS10": 3.0,
            "CPIAUCSL": 100.0 * (1.002**index),
            "UNRATE": 7.0 - index * 0.02,
            "FEDFUNDS": 4.0 - index * 0.01,
        }
        for series_id, value in values.items():
            rows.append(
                MacroVintageObservation(
                    series_id=series_id,
                    observation_date=observation_date,
                    value=f"{value:.8f}",
                    realtime_start=released,
                    realtime_end="9999-12-31",
                )
            )
        year = current.year + (1 if current.month == 12 else 0)
        month = 1 if current.month == 12 else current.month + 1
        current = date(year, month, 1)
    return rows


def _points(days: int = 900) -> list[PricePoint]:
    rows: list[PricePoint] = []
    current = date(2017, 1, 1)
    values = {"SPY": 100.0, "QQQ": 100.0, "TLT": 100.0, "SGOV": 100.0}
    completed = 0
    while completed < days:
        current += timedelta(days=1)
        if current.weekday() >= 5:
            continue
        completed += 1
        values["SPY"] *= 1.0004
        values["QQQ"] *= 1.0007
        values["TLT"] *= 1.0001
        values["SGOV"] *= 1.00004
        for symbol, value in values.items():
            rows.append(
                PricePoint(
                    date=current.isoformat(),
                    symbol=symbol,
                    total_return_index=f"{value:.12f}",
                    available_at=f"{current.isoformat()}T22:00:00+00:00",
                )
            )
    return rows


def _cost_model() -> ExecutionCostModel:
    return ExecutionCostModel(
        schema_version="execution-cost-model-v1",
        commission_bps=1.0,
        minimum_commission_usd=0.0,
        portfolio_notional_usd=10_000.0,
        slippage_tiers=(SlippageTier(None, 1.0),),
        commission_source="test",
        slippage_source="test",
    )


class PointInTimeMacroTests(unittest.TestCase):
    def test_actual_output_type_three_wide_vintage_columns_are_parsed(self) -> None:
        rows = parse_alfred_payload(
            {
                "output_type": 3,
                "observations": [
                    {"date": "2005-01-01", "CPIAUCSL_20100217": "191.600"}
                ],
            },
            series_id="CPIAUCSL",
            raw_manifest_id="manifest-one",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].realtime_start, "2010-02-17")
        self.assertEqual(rows[0].value, "191.600")
        self.assertEqual(rows[0].raw_manifest_id, "manifest-one")

    def test_future_revision_is_not_visible_before_its_vintage_date(self) -> None:
        store = PointInTimeMacroStore(
            [
                MacroVintageObservation(
                    "UNRATE", "2020-01-01", "4.0", "2020-02-07", "2021-01-01"
                ),
                MacroVintageObservation(
                    "UNRATE", "2020-01-01", "5.0", "2021-01-02", "9999-12-31"
                ),
            ],
            publication_lag_days=1,
        )

        self.assertEqual(store.latest("UNRATE", "2020-12-31"), 4.0)
        self.assertEqual(store.latest("UNRATE", "2021-01-02"), 4.0)
        self.assertEqual(store.latest("UNRATE", "2021-01-03"), 5.0)

    def test_macro_regime_uses_month_end_signal_and_next_day_execution(self) -> None:
        config = MacroRegimeConfig(
            risk_on_symbols=("SPY", "QQQ"),
            defensive_symbols=("SGOV", "TLT"),
            cash_symbol="SGOV",
            macro_signal_weights=(
                ("inflation_trend", 0.25),
                ("policy_rate_trend", 0.25),
                ("unemployment_trend", 0.25),
                ("yield_curve", 0.25),
            ),
            signal_lookback_months=3,
            minimum_regime_score=0.0,
            rebalance_frequency="monthly",
            publication_lag_days=1,
            walk_forward_train_days=504,
            walk_forward_test_days=126,
        )
        result = run_macro_regime_backtest(
            _points(),
            _macro_observations(),
            config,
            execution_cost_model=_cost_model(),
        )

        self.assertTrue(result.rebalances)
        self.assertTrue(
            all(item.signal_date < item.effective_date for item in result.rebalances)
        )
        self.assertEqual(
            set(result.rebalances[-1].target_weights), {"SPY", "QQQ"}
        )
        self.assertGreaterEqual(len(result.walk_forward_folds), 2)

    def test_manifest_loader_preserves_fred_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = DataLake(tmp)
            manifest = lake.store_raw(
                source="fred-alfred",
                dataset="series-observation-revisions",
                body={
                    "observations": [
                        {
                            "date": "2020-01-01",
                            "realtime_start": "2020-02-01",
                            "realtime_end": "9999-12-31",
                            "value": "4.0",
                        }
                    ]
                },
                media_type="application/json",
                schema_version="fred-observations-output-type-3-v1",
                available_at="2020-02-01T00:00:00+00:00",
                request={"series_id": "UNRATE", "output_type": 3},
                license_tag="test",
                code_revision="abc123",
                retrieved_at="2020-02-01T00:00:00+00:00",
            )

            rows, manifest_ids = load_alfred_from_manifests(
                Path(tmp) / "catalog" / "manifests"
            )

            self.assertEqual(manifest_ids, [manifest.manifest_id])
            self.assertEqual(rows[0].raw_manifest_id, manifest.manifest_id)
            self.assertEqual(rows[0].series_id, "UNRATE")

    def test_macro_family_passes_through_the_same_candidate_gates(self) -> None:
        hypothesis = {
            "hypothesis_id": "macro-test",
            "strategy_family": "macro_regime",
            "config": {
                "risk_on_symbols": ["SPY", "QQQ"],
                "defensive_symbols": ["SGOV", "TLT"],
                "cash_symbol": "SGOV",
                "macro_signal_weights": {
                    "yield_curve": 0.25,
                    "inflation_trend": 0.25,
                    "unemployment_trend": 0.25,
                    "policy_rate_trend": 0.25,
                },
                "signal_lookback_months": 3,
                "minimum_regime_score": 0.0,
                "rebalance_frequency": "monthly",
                "publication_lag_days": 1,
                "walk_forward_train_days": 504,
                "walk_forward_test_days": 126,
            },
        }

        result = evaluate_hypothesis(
            hypothesis,
            points=_points(),
            macro_observations=_macro_observations(),
            policy=load_research_policy("config/autonomous_research_policy.json"),
            family_size=7,
            data_manifest_ids=["prices", "alfred"],
            code_revision="abc123",
            run_id="macro-run",
            execution_cost_model=_cost_model(),
        )

        self.assertEqual(
            set(result["gates"]),
            {
                "minimum_walk_forward_folds",
                "benchmark_outperformance_ratio",
                "multiple_testing_adjusted_benchmark",
                "double_cost_stress_excess_positive",
            },
        )
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(result["data_manifest_ids"], ["alfred", "prices"])


if __name__ == "__main__":
    unittest.main()
