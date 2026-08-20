import copy
import unittest
from datetime import date, timedelta

from toss_trading.research.variant_perception import (
    build_focused_research_dossier,
    load_focused_research_policy,
    render_focused_research_memo,
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
            "investment_thesis": {
                "statement": "Advanced packaging mix and incremental returns create earnings upside that the current price does not require.",
                "time_horizon_months": 18,
                "pillars": ["WFE growth exceeds the implied cycle path.", "Gross margin and incremental ROIC rise with mix."],
                "recommendation": "buy",
            },
            "variant_view": {
                "market_expectation_summary": "The market prices a conventional WFE recovery with limited sustained mix benefit.",
                "our_variant_summary": "Advanced packaging mix and incremental capital returns should exceed both consensus and price-implied expectations.",
                "expectation_chains": chains,
            },
            "sources": sources,
            "catalyst_path": [
                {"catalyst_id": "earnings", "window_start": self.as_of, "window_end": end, "event": "Earnings and guidance", "observable": "WFE growth, gross margin, packaging mix, capex, and free cash flow", "thesis_resolution": "Reported mix and returns close or invalidate the expectation gap.", "source_ids": ["filing"]},
                {"catalyst_id": "industry_update", "window_start": self.as_of, "window_end": end, "event": "Industry WFE update", "observable": "Revised WFE and advanced packaging demand", "thesis_resolution": "External demand confirms or contradicts the house driver forecast.", "source_ids": ["industry"]},
            ],
            "earnings_model": {
                "forecast_period": "FY2028",
                "market_implied_eps_usd": 9.0,
                "market_implied_eps_source_ids": ["price", "filing"],
                "market_implied_eps_methodology": "Solve the reverse valuation model for FY2028 EPS at the observed price.",
                "consensus_eps_usd": 9.5,
                "consensus_eps_source_ids": ["cons"],
                "consensus_eps_methodology": "Use the point-in-time median FY2028 consensus estimate.",
                "scenarios": [
                    {"name": "bear", "probability": 0.2, "revenue_usd_millions": 30000, "gross_margin_percent": 46, "operating_margin_percent": 27, "eps_usd": 7.5, "free_cash_flow_usd_millions": 5500, "capex_usd_millions": 2500, "key_assumptions": ["WFE contracts.", "Packaging mix stalls."], "source_ids": ["filing", "industry"], "methodology": "Bottom-up segment revenue and margin bridge."},
                    {"name": "base", "probability": 0.5, "revenue_usd_millions": 36000, "gross_margin_percent": 51, "operating_margin_percent": 32, "eps_usd": 10.64, "free_cash_flow_usd_millions": 7800, "capex_usd_millions": 2200, "key_assumptions": ["WFE grows 10%.", "Packaging mix expands."], "source_ids": ["filing", "industry"], "methodology": "Bottom-up segment revenue and margin bridge."},
                    {"name": "bull", "probability": 0.3, "revenue_usd_millions": 41000, "gross_margin_percent": 53, "operating_margin_percent": 35, "eps_usd": 13.0, "free_cash_flow_usd_millions": 9800, "capex_usd_millions": 2200, "key_assumptions": ["HBM demand accelerates.", "Pricing remains firm."], "source_ids": ["filing", "industry"], "methodology": "Bottom-up segment revenue and margin bridge."},
                ],
            },
            "valuation": {
                "framework": "Scenario EPS multiplied by normalized forward P/E with a cross-check to DCF.",
                "cases": [
                    {"name": "bear", "probability": 0.2, "price_target": 190.0, "method": "pe", "equation": "Bear EPS multiplied by 25.3x.", "assumptions": ["Multiple compresses.", "No sustained mix premium."], "source_ids": ["price", "cons"]},
                    {"name": "base", "probability": 0.5, "price_target": 280.0, "method": "pe", "equation": "Base EPS multiplied by 26.3x.", "assumptions": ["Cycle multiple normalizes.", "Mix premium is partial."], "source_ids": ["price", "cons"]},
                    {"name": "bull", "probability": 0.3, "price_target": 350.0, "method": "pe", "equation": "Bull EPS multiplied by 26.9x.", "assumptions": ["Growth persists.", "Mix premium is sustained."], "source_ids": ["price", "cons"]},
                ],
            },
            "risk_disconfirming_evidence": {
                "risks": [
                    {"risk_id": "gm", "statement": "Gross margin fails to expand.", "probability": "medium", "impact": "EPS remains near the implied case.", "monitor": "Gross margin below 49% for two quarters.", "source_ids": ["filing"]},
                    {"risk_id": "orders", "statement": "HBM and DRAM orders slow.", "probability": "medium", "impact": "Revenue driver gap closes.", "monitor": "Backlog and industry orders decline.", "source_ids": ["industry", "filing"]},
                ],
                "contrary_evidence": [{"evidence_id": "capex", "observation": "Capex is rising before the demand proof point.", "thesis_impact": "Free cash flow may be weaker than the base case.", "source_ids": ["filing"]}],
                "monitoring_plan": "Refresh consensus, implied expectations, and the house model after each material disclosure.",
            },
            "position_construction": {
                "initial_weight_percent": 1.0, "target_weight_percent": 3.0, "maximum_weight_percent": 5.0, "earnings_event_weight_percent": 1.5,
                "entry_price_low": 205.0, "entry_price_high": 225.0, "risk_budget_bps": 45,
                "sizing_rationale": "Start below target until the first order catalyst confirms the variant view.",
                "add_conditions": ["Orders and gross margin confirm the base case."],
                "reduce_conditions": ["Price reaches base value before evidence arrives."],
                "exit_conditions": ["Gross margin or backlog crosses a falsification threshold."],
            },
            "score_summary": {"investment_conviction": 78, "summary": "Evidence supports a positive but catalyst-dependent variant view."},
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
        self.assertEqual(list(dossier["research_sections"]), dossier["validation"]["section_order"])
        wfe = next(item for item in dossier["research_sections"]["variant_view"]["expectation_chains"] if item["metric_id"] == "wfe_growth")
        self.assertEqual(wfe["difference"]["house_minus_price_implied"], 5.0)
        self.assertEqual(wfe["difference"]["variant_direction"], "bullish")
        validate_focused_research_dossier(
            dossier, as_of_date=self.as_of, maximum_age_days=14
        )

    def test_rejects_missing_price_implied_chain(self):
        invalid = copy.deepcopy(self.payload)
        invalid["variant_view"]["expectation_chains"][0].pop("price_implied_expectation")
        with self.assertRaisesRegex(ValueError, "implied method"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_rejects_reverse_model_that_does_not_reconcile_to_market_price(self):
        invalid = copy.deepcopy(self.payload)
        invalid["variant_view"]["expectation_chains"][0]["price_implied_expectation"][
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
        dossier["research_sections"]["variant_view"]["our_variant_summary"] = "tampered"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_focused_research_dossier(
                dossier, as_of_date=self.as_of, maximum_age_days=14
            )

    def test_earnings_gaps_and_memo_order_put_score_last(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        earnings = dossier["research_sections"]["earnings_model"]
        self.assertAlmostEqual(earnings["base_vs_consensus_percent"], 12.0)
        memo = render_focused_research_memo(dossier)
        headings = [memo.index(f"## {index}. {name}") for index, name in enumerate([
            "Investment Thesis", "Variant View", "Earnings Model", "Valuation",
            "Catalyst Path", "Risk / Disconfirming Evidence", "Position Construction",
            "Score Summary",
        ], start=1)]
        self.assertEqual(headings, sorted(headings))
        self.assertGreater(memo.index("Investment Conviction"), memo.index("Position Construction"))

    def test_conviction_score_does_not_change_decision_or_position(self):
        low = copy.deepcopy(self.payload)
        high = copy.deepcopy(self.payload)
        low["score_summary"]["investment_conviction"] = 1
        high["score_summary"]["investment_conviction"] = 99
        low_dossier = build_focused_research_dossier(low, policy=self.policy, code_revision="a" * 40)
        high_dossier = build_focused_research_dossier(high, policy=self.policy, code_revision="a" * 40)
        for dossier in (low_dossier, high_dossier):
            self.assertEqual(dossier["research_sections"]["investment_thesis"]["recommendation"], "buy")
            self.assertEqual(dossier["research_sections"]["position_construction"]["target_weight_percent"], 3.0)

    def test_every_decision_section_is_mandatory(self):
        for section in (
            "investment_thesis",
            "variant_view",
            "earnings_model",
            "valuation",
            "catalyst_path",
            "risk_disconfirming_evidence",
            "position_construction",
            "score_summary",
        ):
            invalid = copy.deepcopy(self.payload)
            invalid.pop(section)
            with self.subTest(section=section), self.assertRaises(ValueError):
                build_focused_research_dossier(
                    invalid, policy=self.policy, code_revision="a" * 40
                )


if __name__ == "__main__":
    unittest.main()
