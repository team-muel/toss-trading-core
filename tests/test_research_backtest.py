import json
import tempfile
import unittest
from datetime import date, timedelta

from toss_trading.research import (
    DualMomentumConfig,
    PricePoint,
    run_dual_momentum_backtest,
)
from toss_trading.research.backtest import write_experiment_record
from toss_trading.research.costs import ExecutionCostModel, SlippageTier
from toss_trading.research.reporting import strategy_snapshot


def execution_cost_model(
    commission_bps: float = 10.0, slippage_bps: float = 2.0
) -> ExecutionCostModel:
    return ExecutionCostModel(
        schema_version="execution-cost-model-v1",
        commission_bps=commission_bps,
        minimum_commission_usd=0.0,
        portfolio_notional_usd=10_000.0,
        slippage_tiers=(SlippageTier(None, slippage_bps),),
        commission_source="test_schedule",
        slippage_source="test_policy",
    )


def synthetic_points(days: int = 180) -> list[PricePoint]:
    points = []
    current = date(2025, 1, 1)
    trading_index = 0
    while trading_index < days:
        current += timedelta(days=1)
        if current.weekday() >= 5:
            continue
        trading_index += 1
        values = {
            "SPY": 100 * (1.001 ** trading_index),
            "TLT": 100 * (0.9995 ** trading_index),
            "SGOV": 100 * (1.0001 ** trading_index),
        }
        for symbol, value in values.items():
            points.append(
                PricePoint(
                    date=current.isoformat(),
                    symbol=symbol,
                    total_return_index=f"{value:.12f}",
                    available_at=f"{current.isoformat()}T22:00:00+00:00",
                )
            )
    return points


def prospective_protocol(
    config: DualMomentumConfig,
    *,
    start: str,
    minimum_days: int = 63,
) -> dict:
    return {
        "schema_version": "research-validation-v2",
        "strategy": "broad_etf_dual_momentum_v1",
        "implementation_version": 3,
        "registered_at": "2025-01-01",
        "prospective_oos_start": start,
        "minimum_trading_days": minimum_days,
        "primary_benchmark": "SPY buy-and-hold",
        "execution_cost_policy": {
            "commission": "test_fixed_schedule",
            "slippage": "test_fixed_tiers",
            "artifact_must_record_exact_model": True,
        },
        "prospective_collection_policy": {
            "provider": "tiingo-eod",
            "adjustment": "total_return",
            "maximum_collection_lag_calendar_days": 3,
            "invalid_intervals": [],
        },
        "initial_collection_evidence": [],
        "config": json.loads(json.dumps(config.__dict__)),
    }


def collection_observations(result, *, start: str) -> list[dict]:
    observations = []
    for day, _ in result.daily_returns:
        if day < start:
            continue
        run_id = f"daily-{day}"
        observations.extend(
            [
                {
                    "provider": "tiingo-eod",
                    "adjustment": "total_return",
                    "run_id": run_id,
                    "complete_through_date": day,
                    "collected_at": f"{day}T23:00:00+00:00",
                    "state": "collected",
                },
                {
                    "run_id": run_id,
                    "completed_at": f"{day}T23:01:00+00:00",
                    "state": "run_completed",
                },
            ]
        )
    return observations


class ResearchBacktestTest(unittest.TestCase):
    def test_positive_fold_still_fails_when_spy_return_is_higher(self):
        points = []
        current = date(2024, 1, 1)
        index = 0
        while index < 180:
            current += timedelta(days=1)
            if current.weekday() >= 5:
                continue
            index += 1
            for symbol, growth in {"SPY": 1.001, "TLT": 1.0002, "SGOV": 1.00005}.items():
                points.append(
                    PricePoint(
                        date=current.isoformat(),
                        symbol=symbol,
                        total_return_index=f"{100 * growth**index:.12f}",
                        available_at=f"{current.isoformat()}T22:00:00+00:00",
                    )
                )
        result = run_dual_momentum_backtest(
            points,
            DualMomentumConfig(
                candidate_symbols=("TLT",),
                cash_symbol="SGOV",
                lookback_trading_days=60,
                skip_recent_trading_days=5,
                walk_forward_train_days=63,
                walk_forward_test_days=21,
            ),
            execution_cost_model=execution_cost_model(),
        )
        self.assertTrue(all(fold.metrics["total_return"] > 0 for fold in result.walk_forward_folds))
        self.assertTrue(
            all(not fold.passed_relative_return for fold in result.walk_forward_folds)
        )

    def test_dual_momentum_uses_total_return_history_and_next_day_rebalance(self):
        result = run_dual_momentum_backtest(
            synthetic_points(),
            DualMomentumConfig(
                candidate_symbols=("SPY", "TLT"),
                cash_symbol="SGOV",
                lookback_trading_days=60,
                skip_recent_trading_days=5,
                top_k=1,
                walk_forward_train_days=63,
                walk_forward_test_days=21,
            ),
            execution_cost_model=execution_cost_model(1, 1),
        )

        self.assertGreater(len(result.rebalances), 0)
        self.assertTrue(
            all(
                rebalance.effective_date > rebalance.signal_date
                for rebalance in result.rebalances
            )
        )
        self.assertEqual(
            {next(iter(item.target_weights)) for item in result.rebalances},
            {"SPY"},
        )
        self.assertGreater(result.metrics["total_return"], 0)
        self.assertGreater(result.metrics["turnover"], 0)
        self.assertAlmostEqual(result.rebalances[0].cost_fraction, 0.0004)
        self.assertEqual(
            set(result.benchmark_metrics),
            {"SPY buy-and-hold", "equal-weight candidates", "60/40", "cash"},
        )
        self.assertGreater(
            result.benchmark_metrics["SPY buy-and-hold"]["total_return"],
            result.benchmark_metrics["cash"]["total_return"],
        )
        self.assertGreaterEqual(len(result.walk_forward_folds), 4)
        self.assertTrue(
            all(
                fold.train_end < fold.test_start
                for fold in result.walk_forward_folds
            )
        )
        self.assertTrue(
            all(
                fold.benchmark_name == "SPY buy-and-hold"
                and "annualized_mean_excess" in fold.excess_metrics
                for fold in result.walk_forward_folds
            )
        )

    def test_point_in_time_availability_violation_is_rejected(self):
        points = synthetic_points()
        points = [
            PricePoint(
                date=point.date,
                symbol=point.symbol,
                total_return_index=point.total_return_index,
                available_at="2099-01-01T00:00:00+00:00"
                if point.symbol == "SPY"
                else point.available_at,
            )
            for point in points
        ]
        config = DualMomentumConfig(
            candidate_symbols=("SPY", "TLT"),
            lookback_trading_days=60,
            skip_recent_trading_days=0,
        )

        with self.assertRaisesRegex(ValueError, "point-in-time violation"):
            run_dual_momentum_backtest(
                points, config, execution_cost_model=execution_cost_model()
            )

    def test_next_day_close_execution_does_not_capture_prior_close_return(self):
        config = DualMomentumConfig(
            candidate_symbols=("SPY", "TLT"),
            cash_symbol="SGOV",
            lookback_trading_days=60,
            skip_recent_trading_days=5,
            top_k=1,
        )
        points = synthetic_points()
        costs = execution_cost_model(1, 1)
        baseline = run_dual_momentum_backtest(
            points, config, execution_cost_model=costs
        )
        first_effective_date = baseline.rebalances[0].effective_date
        mutated = [
            PricePoint(
                date=point.date,
                symbol=point.symbol,
                total_return_index=(
                    str(float(point.total_return_index) * 2)
                    if point.date == first_effective_date and point.symbol == "SPY"
                    else point.total_return_index
                ),
                available_at=point.available_at,
            )
            for point in points
        ]

        shocked = run_dual_momentum_backtest(
            mutated, config, execution_cost_model=costs
        )

        baseline_returns = dict(baseline.daily_returns)
        shocked_returns = dict(shocked.daily_returns)
        self.assertAlmostEqual(
            shocked_returns[first_effective_date],
            baseline_returns[first_effective_date],
            places=12,
        )

    def test_experiment_record_contains_data_and_code_provenance(self):
        result = run_dual_momentum_backtest(
            synthetic_points(),
            DualMomentumConfig(
                candidate_symbols=("SPY", "TLT"),
                lookback_trading_days=60,
                skip_recent_trading_days=5,
                walk_forward_train_days=63,
                walk_forward_test_days=21,
            ),
            execution_cost_model=execution_cost_model(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_experiment_record(
                result,
                output_root=tmp,
                data_manifest_ids=["manifest-2", "manifest-1"],
                code_revision="abc123",
                benchmark_names=["SPY buy-and-hold", "60/40"],
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["code_revision"], "abc123")
            self.assertEqual(payload["input_adjustment"], "total_return")
            self.assertEqual(
                payload["data_manifest_ids"],
                ["manifest-1", "manifest-2"],
            )
            self.assertIn("max_drawdown", payload["metrics"])
            self.assertEqual(
                set(payload["benchmark_metrics"]),
                {"SPY buy-and-hold", "60/40"},
            )
            self.assertGreater(len(payload["walk_forward_folds"]), 0)

    def test_prospective_metrics_remain_hidden_until_minimum_sample(self):
        config = DualMomentumConfig(
            candidate_symbols=("SPY", "TLT"),
            cash_symbol="SGOV",
            lookback_trading_days=60,
            skip_recent_trading_days=5,
            walk_forward_train_days=63,
            walk_forward_test_days=21,
        )
        result = run_dual_momentum_backtest(
            synthetic_points(), config, execution_cost_model=execution_cost_model()
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_experiment_record(
                result,
                output_root=tmp,
                data_manifest_ids=["manifest-1"],
                code_revision="abc123",
                benchmark_names=["SPY buy-and-hold"],
                validation_protocol=prospective_protocol(
                    config,
                    start="2026-08-03",
                ),
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertIsNone(payload["metrics"])
            self.assertEqual(payload["benchmark_metrics"], {})
            self.assertEqual(payload["prospective_holdout"]["state"], "collecting")
            self.assertFalse(
                payload["prospective_holdout"]["metrics_revealed"]
            )
            self.assertIn("total_return", payload["full_sample_metrics"])
            self.assertEqual(
                payload["validation_protocol"]["diagnostic_metrics_scope"],
                "historical_pre_holdout_only",
            )

    def test_completed_prospective_sample_becomes_headline_metrics(self):
        config = DualMomentumConfig(
            candidate_symbols=("SPY", "TLT"),
            cash_symbol="SGOV",
            lookback_trading_days=60,
            skip_recent_trading_days=5,
            walk_forward_train_days=63,
            walk_forward_test_days=21,
        )
        result = run_dual_momentum_backtest(
            synthetic_points(), config, execution_cost_model=execution_cost_model()
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_experiment_record(
                result,
                output_root=tmp,
                data_manifest_ids=["manifest-1"],
                code_revision="abc123",
                benchmark_names=["SPY buy-and-hold"],
                validation_protocol=prospective_protocol(
                    config,
                    start="2025-04-01",
                ),
                prospective_observations=collection_observations(
                    result,
                    start="2025-04-01",
                ),
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["prospective_holdout"]["state"], "completed")
            self.assertTrue(payload["prospective_holdout"]["metrics_revealed"])
            self.assertEqual(payload["metrics"]["trading_days"], 63.0)
            self.assertEqual(
                payload["benchmark_metrics"]["SPY buy-and-hold"][
                    "trading_days"
                ],
                63.0,
            )
            snapshot = strategy_snapshot(
                path,
                expected_code_revision="abc123",
                available_manifest_ids={"manifest-1"},
            )
            self.assertEqual(snapshot["methodology_state"], "passed")
            self.assertEqual(snapshot["benchmark_state"], "failed")
            self.assertEqual(snapshot["promotion_state"], "blocked")

    def test_collecting_holdout_does_not_leak_partial_metrics_or_curve(self):
        config = DualMomentumConfig(
            candidate_symbols=("SPY", "TLT"),
            cash_symbol="SGOV",
            lookback_trading_days=60,
            skip_recent_trading_days=5,
            walk_forward_train_days=63,
            walk_forward_test_days=21,
        )
        result = run_dual_momentum_backtest(
            synthetic_points(), config, execution_cost_model=execution_cost_model()
        )
        start = result.daily_returns[-20][0]
        expected_historical_days = sum(
            1 for day, _ in result.daily_returns if day < start
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_experiment_record(
                result,
                output_root=tmp,
                data_manifest_ids=["manifest-1"],
                code_revision="abc123",
                benchmark_names=["SPY buy-and-hold"],
                validation_protocol=prospective_protocol(config, start=start),
                prospective_observations=collection_observations(
                    result,
                    start=start,
                ),
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertIsNone(payload["metrics"])
            self.assertEqual(
                payload["full_sample_metrics"]["trading_days"],
                float(expected_historical_days),
            )
            self.assertLess(payload["equity_curve"][-1][0], start)
            self.assertTrue(
                all(item["effective_date"] < start for item in payload["rebalances"])
            )

    def test_late_backfill_marks_holdout_invalid_and_hides_metrics(self):
        config = DualMomentumConfig(
            candidate_symbols=("SPY", "TLT"),
            cash_symbol="SGOV",
            lookback_trading_days=60,
            skip_recent_trading_days=5,
            walk_forward_train_days=63,
            walk_forward_test_days=21,
        )
        result = run_dual_momentum_backtest(
            synthetic_points(), config, execution_cost_model=execution_cost_model()
        )
        start = result.daily_returns[-20][0]
        observations = collection_observations(result, start=start)
        for observation in observations:
            if observation["state"] == "collected":
                observation["collected_at"] = "2099-01-01T00:00:00+00:00"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_experiment_record(
                result,
                output_root=tmp,
                data_manifest_ids=["manifest-1"],
                code_revision="abc123",
                benchmark_names=["SPY buy-and-hold"],
                validation_protocol=prospective_protocol(config, start=start),
                prospective_observations=observations,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(
                payload["prospective_holdout"]["state"],
                "invalid_data_gap",
            )
            self.assertEqual(
                payload["prospective_holdout"]["observed_trading_days"],
                0,
            )
            self.assertIsNone(payload["metrics"])


if __name__ == "__main__":
    unittest.main()
