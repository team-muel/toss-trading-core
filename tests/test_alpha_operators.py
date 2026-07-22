import unittest

from toss_trading.alpha import operators as ops


class CrossSectionalOperatorTest(unittest.TestCase):
    def test_rank_normalises_to_unit_interval_with_average_ties(self):
        result = ops.rank({"a": 10.0, "b": 20.0, "c": 20.0, "d": 5.0})
        self.assertEqual(result["d"], 0.0)
        self.assertEqual(result["a"], 1 / 3)
        # b and c tie for the top two positions -> average rank
        self.assertAlmostEqual(result["b"], result["c"])
        self.assertAlmostEqual(result["b"], (3.5 - 1) / 3)

    def test_rank_single_symbol_is_midpoint(self):
        self.assertEqual(ops.rank({"only": 42.0}), {"only": 0.5})

    def test_zscore_zero_variance_returns_zeros(self):
        self.assertEqual(ops.zscore({"a": 3.0, "b": 3.0}), {"a": 0.0, "b": 0.0})

    def test_scale_makes_unit_gross_exposure(self):
        scaled = ops.scale({"a": 3.0, "b": -1.0})
        self.assertAlmostEqual(sum(abs(v) for v in scaled.values()), 1.0)

    def test_group_neutralize_removes_group_mean(self):
        values = {"a": 1.0, "b": 3.0, "c": 10.0, "d": 20.0}
        groups = {"a": "x", "b": "x", "c": "y", "d": "y"}
        result = ops.group_neutralize(values, groups)
        self.assertAlmostEqual(result["a"], -1.0)
        self.assertAlmostEqual(result["b"], 1.0)
        self.assertAlmostEqual(result["c"], -5.0)
        self.assertAlmostEqual(result["d"], 5.0)

    def test_winsorize_clips_outliers(self):
        values = {f"s{i}": 1.0 for i in range(9)}
        values["s9"] = 1000.0
        winsorized = ops.winsorize(values, std=2.0)
        self.assertLess(winsorized["s9"], 1000.0)

    def test_truncate_caps_and_keeps_unit_gross_when_feasible(self):
        weights = ops.truncate({"a": 100.0, "b": 1.0, "c": 1.0}, max_weight=0.4)
        self.assertLessEqual(max(abs(v) for v in weights.values()), 0.4 + 1e-9)
        self.assertAlmostEqual(sum(abs(v) for v in weights.values()), 1.0, places=6)
        self.assertAlmostEqual(weights["a"], 0.4)
        self.assertAlmostEqual(weights["b"], 0.3)
        self.assertAlmostEqual(weights["c"], 0.3)

    def test_truncate_underinvests_when_too_few_names(self):
        # only two active names, cap 0.4 -> max gross 0.8, never violate the cap
        weights = ops.truncate({"a": 5.0, "b": 1.0}, max_weight=0.4)
        self.assertLessEqual(max(abs(v) for v in weights.values()), 0.4 + 1e-9)
        self.assertAlmostEqual(sum(abs(v) for v in weights.values()), 0.8, places=6)

    def test_truncate_rejects_bad_bounds(self):
        with self.assertRaises(ValueError):
            ops.truncate({"a": 1.0}, max_weight=0.0)


class TimeSeriesOperatorTest(unittest.TestCase):
    def test_ts_delta_and_delay_warmup_is_none(self):
        series = [1.0, 2.0, 4.0, 7.0]
        self.assertEqual(ops.ts_delay(series, 1), [None, 1.0, 2.0, 4.0])
        self.assertEqual(ops.ts_delta(series, 1), [None, 1.0, 2.0, 3.0])

    def test_ts_mean_trailing_window(self):
        series = [2.0, 4.0, 6.0, 8.0]
        self.assertEqual(ops.ts_mean(series, 2), [None, 3.0, 5.0, 7.0])

    def test_ts_stddev_requires_two(self):
        with self.assertRaises(ValueError):
            ops.ts_stddev([1.0, 2.0], 1)

    def test_ts_rank_positions_current_value(self):
        series = [10.0, 20.0, 30.0, 5.0]
        ranks = ops.ts_rank(series, 3)
        self.assertIsNone(ranks[1])
        self.assertEqual(ranks[2], 1.0)  # 30 is the max of [10,20,30]
        self.assertEqual(ranks[3], 0.0)  # 5 is the min of [20,30,5]

    def test_ts_decay_linear_weights_recent_higher(self):
        series = [1.0, 0.0, 0.0]
        decay = ops.ts_decay_linear(series, 3)
        # weights (1,2,3): (1*1 + 2*0 + 3*0)/6
        self.assertAlmostEqual(decay[2], 1 / 6)


if __name__ == "__main__":
    unittest.main()
