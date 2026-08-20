import copy
import unittest
from datetime import date, timedelta

from toss_trading.research.stock_recommendations import (
    generate_stock_recommendations,
    load_recommendation_policy,
)
from toss_trading.research.variant_perception import (
    build_focused_research_dossier,
    load_focused_research_policy,
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
        self.policy["maximum_screening_candidates"] = 2
        self.policy["maximum_buy_recommendations"] = 2
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

    def focused_dossier(self, as_of: str):
        sources = [
            {"source_id": "px", "source_type": "market_price", "organization": "Exchange", "observed_at": as_of, "locator": "close"},
            {"source_id": "consensus", "source_type": "consensus_dataset", "organization": "Consensus Provider", "observed_at": as_of, "locator": "snapshot"},
            {"source_id": "filing", "source_type": "company_filing", "organization": "AAA", "observed_at": as_of, "locator": "10-Q"},
            {"source_id": "industry", "source_type": "industry_dataset", "organization": "Industry Body", "observed_at": as_of, "locator": "industry outlook"},
        ]
        chains = []
        for metric_id, category, consensus, implied, house in [
            ("driver", "industry_or_revenue_driver", 8.0, 6.0, 10.0),
            ("margin", "margin_or_cash_flow", 30.0, 29.0, 32.0),
            ("roic", "capital_efficiency_or_valuation", 15.0, 13.0, 18.0),
        ]:
            chains.append({
                "metric_id": metric_id,
                "metric_name": metric_id,
                "category": category,
                "horizon": "FY2027",
                "unit": "percent",
                "favorable_direction": "higher",
                "market_consensus": {"value": consensus, "source_ids": ["consensus"], "methodology": "Point-in-time median consensus."},
                "price_implied_expectation": {
                    "value": implied,
                    "source_ids": ["px", "filing"],
                    "methodology": "Solve the valuation model for this metric while holding stated assumptions fixed.",
                    "method": "reverse_dcf",
                    "model_price_at_implied": 30.0,
                    "equation": "Current enterprise value equals discounted forecast cash flows and terminal value.",
                    "assumptions": [
                        {"name": "discount rate", "value": 9.0, "unit": "percent", "source_ids": ["px"]},
                        {"name": "terminal growth", "value": 3.0, "unit": "percent", "source_ids": ["filing"]},
                    ],
                },
                "house_estimate": {"value": house, "source_ids": ["filing", "industry"], "methodology": "Driver-based estimate using disclosed capacity and industry demand."},
                "why_market_may_be_wrong": "The market extrapolates the current mix and underweights the disclosed higher-return capacity ramp.",
                "falsification_criteria": ["Two consecutive quarters miss the disclosed capacity ramp."],
                "catalyst_ids": ["earnings"],
            })
        payload = {
            "symbol": "AAA",
            "as_of_date": as_of,
            "current_price": 30.0,
            "currency": "USD",
            "price_source_ids": ["px"],
            "market_narrative": "The market expects a modest cycle recovery with limited incremental returns.",
            "variant_summary": "Capacity mix should lift growth, margin, and incremental returns above price-implied levels.",
            "sources": sources,
            "variant_chains": chains,
            "catalysts": [{
                "catalyst_id": "earnings",
                "window_start": as_of,
                "window_end": as_of,
                "event": "Quarterly results",
                "observable": "Revenue mix, gross margin, and incremental return disclosures",
                "thesis_resolution": "The reported mix either closes or invalidates the modeled expectation gap.",
                "source_ids": ["filing"],
            }],
            "scenario_analysis": [
                {"name": "bear", "probability": 0.2, "price_target": 20.0, "thesis": "Capacity is underutilized."},
                {"name": "base", "probability": 0.5, "price_target": 36.0, "thesis": "Mix develops as estimated."},
                {"name": "bull", "probability": 0.3, "price_target": 48.0, "thesis": "Demand and mix both surprise higher."},
            ],
            "recommendation": "buy",
            "monitoring_plan": "Refresh consensus and reverse valuation after every earnings release.",
        }
        return build_focused_research_dossier(
            payload,
            policy=load_focused_research_policy("config/focused_research_policy.json"),
            code_revision="b" * 40,
        )

    def test_screening_candidates_are_not_buy_recommendations_without_dossier(self):
        as_of, rows = self.rows()
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            source_manifest_ids=["manifest-1"],
        )
        self.assertEqual(result["universe"]["ranked_count"], 4)
        self.assertEqual(len(result["screening_candidates"]), 2)
        self.assertEqual(result["screening_candidates"][0]["symbol"], "AAA")
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(
            result["recommendation_gate"]["withheld_pending_focused_research_count"],
            2,
        )
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(
            result["prospective_tracking"]["performance_revealed"]
        )

    def test_buy_recommendation_requires_passed_variant_perception_dossier(self):
        as_of, rows = self.rows()
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[self.focused_dossier(as_of)],
        )
        self.assertEqual([item["symbol"] for item in result["recommendations"]], ["AAA"])
        self.assertEqual(
            result["recommendations"][0]["focused_research_state"],
            "buy_dossier_passed",
        )
        self.assertGreater(
            result["recommendations"][0]["probability_weighted_return"], 0
        )

    def test_stale_dossier_returns_to_research_queue_instead_of_failing_run(self):
        as_of, rows = self.rows()
        stale_date = (date.fromisoformat(as_of) - timedelta(days=30)).isoformat()
        result = generate_stock_recommendations(
            rows,
            policy=self.policy,
            as_of_date=as_of,
            code_revision="a" * 40,
            focused_research_dossiers=[self.focused_dossier(stale_date)],
            focused_research_maximum_age_days=14,
        )
        self.assertEqual(result["recommendations"], [])
        aaa = next(
            item for item in result["screening_candidates"] if item["symbol"] == "AAA"
        )
        self.assertEqual(aaa["focused_research_state"], "focused_research_stale")

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
