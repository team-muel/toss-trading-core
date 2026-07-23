import unittest
from math import sqrt

from toss_trading.alpha import metrics


class MetricsTest(unittest.TestCase):
    def test_daily_pnl_dots_weights_with_returns(self):
        positions = {"a": [0.5, 0.5], "b": [0.5, -0.5]}
        forward = {"a": [0.02, -0.01], "b": [0.00, 0.04]}
        pnl = metrics.daily_pnl(positions, forward)
        self.assertAlmostEqual(pnl[0], 0.01)
        self.assertAlmostEqual(pnl[1], -0.025)

    def test_daily_pnl_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            metrics.daily_pnl({"a": [0.1]}, {"a": [0.1, 0.2]})

    def test_sharpe_of_constant_positive_pnl_is_zero_std(self):
        self.assertEqual(metrics.sharpe([0.01, 0.01, 0.01]), 0.0)

    def test_sharpe_annualises(self):
        pnl = [0.01, -0.01, 0.02, -0.02, 0.015]
        mean = sum(pnl) / len(pnl)
        var = sum((v - mean) ** 2 for v in pnl) / (len(pnl) - 1)
        expected = sqrt(252) * mean / sqrt(var)
        self.assertAlmostEqual(metrics.sharpe(pnl), expected)

    def test_turnover_of_static_book_is_zero(self):
        self.assertEqual(metrics.turnover({"a": [0.5, 0.5, 0.5], "b": [0.5, 0.5, 0.5]}), 0.0)

    def test_turnover_full_flip(self):
        # book flips from a to b each step -> traded == gross
        positions = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
        self.assertAlmostEqual(metrics.turnover(positions), 2.0)

    def test_fitness_matches_brain_formula(self):
        value = metrics.fitness(2.0, 0.20, 0.50)
        self.assertAlmostEqual(value, 2.0 * sqrt(0.20 / 0.50))

    def test_fitness_applies_turnover_floor(self):
        low = metrics.fitness(1.0, 0.10, 0.01)
        floored = 1.0 * sqrt(0.10 / metrics.TURNOVER_FLOOR)
        self.assertAlmostEqual(low, floored)

    def test_max_drawdown_non_negative(self):
        pnl = [0.05, -0.10, 0.02, -0.03]
        self.assertGreater(metrics.max_drawdown(pnl), 0.0)

    def test_is_os_split_holds_out_tail(self):
        in_sample, out_sample = metrics.is_os_split(10, 0.3)
        self.assertEqual(list(in_sample), list(range(0, 7)))
        self.assertEqual(list(out_sample), list(range(7, 10)))

    def test_evaluate_bundles_metrics(self):
        positions = {"a": [0.5, 0.5, 0.5], "b": [0.5, 0.5, 0.5]}
        forward = {"a": [0.01, 0.02, -0.01], "b": [0.00, -0.01, 0.02]}
        result = metrics.evaluate(positions, forward, book_size=1.0)
        self.assertEqual(result.periods, 3)
        self.assertIn("fitness", result.summary())


if __name__ == "__main__":
    unittest.main()
