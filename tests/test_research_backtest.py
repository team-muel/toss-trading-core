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


class ResearchBacktestTest(unittest.TestCase):
    def test_dual_momentum_uses_total_return_history_and_next_day_rebalance(self):
        result = run_dual_momentum_backtest(
            synthetic_points(),
            DualMomentumConfig(
                candidate_symbols=("SPY", "TLT"),
                cash_symbol="SGOV",
                lookback_trading_days=60,
                skip_recent_trading_days=5,
                top_k=1,
                commission_bps=1,
                slippage_bps=1,
            ),
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
            run_dual_momentum_backtest(points, config)

    def test_experiment_record_contains_data_and_code_provenance(self):
        result = run_dual_momentum_backtest(
            synthetic_points(),
            DualMomentumConfig(
                candidate_symbols=("SPY", "TLT"),
                lookback_trading_days=60,
                skip_recent_trading_days=5,
            ),
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
            self.assertEqual(
                payload["data_manifest_ids"],
                ["manifest-1", "manifest-2"],
            )
            self.assertIn("max_drawdown", payload["metrics"])


if __name__ == "__main__":
    unittest.main()
