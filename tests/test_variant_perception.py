import copy
import unittest
from datetime import date, timedelta

from toss_trading.research.variant_perception import (
    build_focused_research_dossier,
    load_focused_research_policy,
    validate_focused_research_dossier,
)


class VariantPerceptionTest(unittest.TestCase):
    def setUp(self):
        self.as_of = "2026-08-20"
        self.policy = load_focused_research_policy(
            "config/focused_research_policy.json"
        )
        self.payload = self._payload()

    def _payload(self):
        sources = [
            {"source_id": "price", "source_type": "market_price", "organization": "NASDAQ", "observed_at": self.as_of, "locator": "official close"},
            {"source_id": "cons", "source_type": "consensus_dataset", "organization": "Consensus Vendor", "observed_at": self.as_of, "locator": "point-in-time snapshot"},
            {"source_id": "filing", "source_type": "company_filing", "organization": "AMAT", "observed_at": self.as_of, "locator": "10-Q accession"},
            {"source_id": "industry", "source_type": "industry_dataset", "organization": "SEMI", "observed_at": self.as_of, "locator": "WFE dataset"},
        ]
        chains = []
        for metric_id, name, category, implied, consensus, house in [
            ("wfe_growth", "WFE growth", "industry_or_revenue_driver", 5.0, 7.0, 10.0),
            ("gross_margin", "Gross margin", "margin_or_cash_flow", 48.0, 49.0, 51.0),
            ("incremental_roic", "Incremental ROIC", "capital_efficiency_or_valuation", 12.0, 14.0, 18.0),
        ]:
            chains.append({
                "metric_id": metric_id,
                "metric_name": name,
                "category": category,
                "horizon": "FY2028",
                "unit": "percent",
                "favorable_direction": "higher",
                "market_consensus": {"value": consensus, "source_ids": ["cons"], "methodology": "Median sell-side estimate captured at the as-of time."},
                "price_implied_expectation": {
                    "value": implied,
                    "source_ids": ["price", "filing"],
                    "methodology": "Solve the reverse DCF for the named operating variable.",
                    "method": "reverse_dcf",
                    "model_price_at_implied": 220.0,
                    "equation": "Enterprise value equals present value of explicit cash flows plus terminal value.",
                    "assumptions": [
                        {"name": "WACC", "value": 9.0, "unit": "percent", "source_ids": ["price"]},
                        {"name": "terminal growth", "value": 3.0, "unit": "percent", "source_ids": ["filing"]},
                    ],
                },
                "house_estimate": {"value": house, "source_ids": ["filing", "industry"], "methodology": "Bottom-up segment and capacity model."},
                "why_market_may_be_wrong": "The market underweights advanced packaging mix and the return on incremental capacity.",
                "falsification_criteria": ["Advanced packaging share fails to increase by the next two reported quarters."],
                "catalyst_ids": ["earnings", "industry_update"],
            })
        end = (date.fromisoformat(self.as_of) + timedelta(days=90)).isoformat()
        return {
            "symbol": "AMAT",
            "as_of_date": self.as_of,
            "current_price": 220.0,
            "currency": "USD",
            "price_source_ids": ["price"],
            "market_narrative": "The market prices a conventional WFE recovery with limited sustained mix benefit.",
            "variant_summary": "Advanced packaging mix and incremental capital returns should exceed both consensus and price-implied expectations.",
            "sources": sources,
            "variant_chains": chains,
            "catalysts": [
                {"catalyst_id": "earnings", "window_start": self.as_of, "window_end": end, "event": "Earnings and guidance", "observable": "WFE growth, gross margin, packaging mix, capex, and free cash flow", "thesis_resolution": "Reported mix and returns close or invalidate the expectation gap.", "source_ids": ["filing"]},
                {"catalyst_id": "industry_update", "window_start": self.as_of, "window_end": end, "event": "Industry WFE update", "observable": "Revised WFE and advanced packaging demand", "thesis_resolution": "External demand confirms or contradicts the house driver forecast.", "source_ids": ["industry"]},
            ],
            "scenario_analysis": [
                {"name": "bear", "probability": 0.2, "price_target": 170.0, "thesis": "WFE and packaging slow together."},
                {"name": "base", "probability": 0.5, "price_target": 250.0, "thesis": "House operating estimates are realized."},
                {"name": "bull", "probability": 0.3, "price_target": 310.0, "thesis": "Mix and industry growth both exceed the house case."},
            ],
            "recommendation": "buy",
            "monitoring_plan": "Refresh consensus, implied expectations, and the house model after each material disclosure.",
        }

    def test_builds_complete_expectation_chain_and_computed_gap(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        self.assertEqual(dossier["validation"]["state"], "passed")
        self.assertEqual(
            dossier["validation"]["chain_order"],
            ["market_consensus", "price_implied_expectation", "house_estimate", "difference", "catalysts"],
        )
        wfe = next(item for item in dossier["variant_chains"] if item["metric_id"] == "wfe_growth")
        self.assertEqual(wfe["difference"]["house_minus_price_implied"], 5.0)
        self.assertEqual(wfe["difference"]["variant_direction"], "bullish")
        validate_focused_research_dossier(
            dossier, as_of_date=self.as_of, maximum_age_days=14
        )

    def test_rejects_missing_price_implied_chain(self):
        invalid = copy.deepcopy(self.payload)
        invalid["variant_chains"][0].pop("price_implied_expectation")
        with self.assertRaisesRegex(ValueError, "implied method"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_rejects_reverse_model_that_does_not_reconcile_to_market_price(self):
        invalid = copy.deepcopy(self.payload)
        invalid["variant_chains"][0]["price_implied_expectation"][
            "model_price_at_implied"
        ] = 180.0
        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_rejects_future_or_stale_sources(self):
        future = copy.deepcopy(self.payload)
        future["sources"][0]["observed_at"] = "2026-08-21"
        with self.assertRaisesRegex(ValueError, "future"):
            build_focused_research_dossier(
                future, policy=self.policy, code_revision="a" * 40
            )
        stale = copy.deepcopy(self.payload)
        stale["sources"][1]["observed_at"] = "2026-06-01"
        with self.assertRaisesRegex(ValueError, "stale"):
            build_focused_research_dossier(
                stale, policy=self.policy, code_revision="a" * 40
            )

    def test_tampering_breaks_recommendation_gate(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        dossier["variant_summary"] = "tampered"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_focused_research_dossier(
                dossier, as_of_date=self.as_of, maximum_age_days=14
            )


if __name__ == "__main__":
    unittest.main()
