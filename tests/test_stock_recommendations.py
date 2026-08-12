import copy
import unittest
from datetime import date, timedelta

from toss_trading.research.stock_recommendations import (
    generate_stock_recommendations,
    load_recommendation_policy,
)


def trading_dates(count: int) -> list[str]:
    current = date(2025, 8, 18)
    result = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


class StockRecommendationTest(unittest.TestCase):
    def setUp(self):
        base = load_recommendation_policy(
            "config/stock_recommendation_policy.json"
        )
        self.policy = copy.deepcopy(base)
        self.policy["minimum_universe_size"] = 3
        self.policy["target_universe_size"] = 4
        self.policy["maximum_universe_size"] = 5
        self.policy["maximum_recommendations"] = 2
        self.policy["minimum_median_dollar_volume_usd"] = 1

    def rows(self):
        dates = trading_dates(253)
        rows = []
        for symbol, slope, volatility in [
            ("AAA", 0.003, 0.0),
            ("BBB", 0.002, 0.001),
            ("CCC", 0.001, 0.002),
            ("DDD", -0.001, 0.0),
        ]:
            price = 20.0
            for index, market_date in enumerate(dates):
                price *= 1 + slope + (volatility if index % 2 else -volatility)
                rows.append(
                    {
                        "symbol": symbol,
                        "exchange_local_date": market_date,
                        "close": price,
                        "volume": 1_000_000,
                    }
                )
        return dates[-1], rows

    def test_generates_bounded_research_only_recommendations(self):
        as_of, rows = self.rows()
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            source_manifest_ids=["manifest-1"],
        )
        self.assertEqual(result["universe"]["ranked_count"], 4)
        self.assertEqual(len(result["recommendations"]), 2)
        self.assertEqual(result["recommendations"][0]["symbol"], "AAA")
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(
            result["prospective_tracking"]["performance_revealed"]
        )

    def test_refuses_too_small_universe(self):
        as_of, rows = self.rows()
        self.policy["minimum_universe_size"] = 5
        with self.assertRaisesRegex(ValueError, "smaller"):
            generate_stock_recommendations(
                rows,
                policy=self.policy,
                as_of_date=as_of,
                code_revision="a" * 40,
            )

    def test_checked_in_policy_is_gated_at_2500_names(self):
        policy = load_recommendation_policy(
            "config/stock_recommendation_policy.json"
        )
        self.assertFalse(policy["enabled"])
        self.assertEqual(policy["target_universe_size"], 2500)
        self.assertGreaterEqual(policy["minimum_universe_size"], 2000)
        self.assertLessEqual(policy["maximum_universe_size"], 3000)
        self.assertFalse(policy["execution_authorized"])


if __name__ == "__main__":
    unittest.main()
