import copy
import unittest
from datetime import date, timedelta

from toss_trading.research.variant_perception import (
    build_focused_research_dossier,
    load_focused_research_policy,
    render_focused_research_memo,
    validate_focused_research_dossier,
)
from tests.focused_research_fixtures import earnings_call_payload


def earnings_quality_payload():
    adjustments = [
        ("stock_based_compensation", "Stock-based compensation", "operating", -500, -600, -400, -492),
        ("restructuring", "Restructuring", "operating", -100, -80, -80, -65.6),
        ("acquisition_adjustment", "Acquisition adjustment", "operating", -50, -70, -40, -57.4),
        ("tax_benefit", "Tax benefit", "tax", 0, 0, 100, 120),
        ("one_off_gain", "One-off gain", "non_operating", 75, 100, 60, 82),
    ]
    adjustment_rows = [
        {
            "adjustment_id": adjustment_id,
            "label": label,
            "income_statement_location": location,
            "prior_pretax_impact_usd_millions": prior_pretax,
            "current_pretax_impact_usd_millions": current_pretax,
            "prior_after_tax_impact_usd_millions": prior_after_tax,
            "current_after_tax_impact_usd_millions": current_after_tax,
            "source_ids": ["filing"],
            "methodology": "Reconcile the disclosed GAAP impact to the normalized earnings bridge.",
        }
        for (
            adjustment_id,
            label,
            location,
            prior_pretax,
            current_pretax,
            prior_after_tax,
            current_after_tax,
        ) in adjustments
    ]

    def bridge(period):
        current = period == "current"
        core_operating_income = 9000 if current else 8000
        recurring_non_operating = -100
        tax_rate = 18 if current else 20
        shares = 800 if current else 840
        prefix = "current" if current else "prior"
        core_pretax = core_operating_income + recurring_non_operating
        core_net_income = core_pretax * (1 - tax_rate / 100)
        operating_adjustments = sum(
            row[f"{prefix}_pretax_impact_usd_millions"]
            for row in adjustment_rows
            if row["income_statement_location"] == "operating"
        )
        pretax_adjustments = sum(
            row[f"{prefix}_pretax_impact_usd_millions"]
            for row in adjustment_rows
            if row["income_statement_location"] in {"operating", "non_operating"}
        )
        after_tax_adjustments = sum(
            row[f"{prefix}_after_tax_impact_usd_millions"]
            for row in adjustment_rows
        )
        gaap_net_income = core_net_income + after_tax_adjustments
        return {
            "core_operating_income_usd_millions": core_operating_income,
            "reported_gaap_operating_income_usd_millions": core_operating_income
            + operating_adjustments,
            "recurring_non_operating_income_usd_millions": recurring_non_operating,
            "normalized_tax_rate_percent": tax_rate,
            "reported_gaap_pretax_income_usd_millions": core_pretax
            + pretax_adjustments,
            "reported_gaap_net_income_usd_millions": gaap_net_income,
            "reported_gaap_eps_usd": gaap_net_income / shares,
            "reported_non_gaap_net_income_usd_millions": core_net_income,
            "reported_non_gaap_eps_usd": core_net_income / shares,
            "diluted_shares_millions": shares,
            "buyback_attributable_weighted_average_share_reduction_millions": (
                18 if current else 0
            ),
        }

    return {
        "periods": {"prior_period": "FY2026", "current_period": "FY2027"},
        "balance_sheet": {
            "prior": {"revenue_usd_millions": 30000, "accounts_receivable_usd_millions": 4000, "inventory_usd_millions": 3000, "contract_liabilities_usd_millions": 2000, "deferred_revenue_usd_millions": 1000},
            "current": {"revenue_usd_millions": 36000, "accounts_receivable_usd_millions": 5200, "inventory_usd_millions": 3900, "contract_liabilities_usd_millions": 2600, "deferred_revenue_usd_millions": 1250},
            "source_ids": ["filing"],
            "methodology": "Compare reported period-end balances with the corresponding annual revenue periods.",
        },
        "cash_conversion": {
            "prior": {"operating_cash_flow_usd_millions": 6500, "total_assets_usd_millions": 40000, "working_capital_contribution_usd_millions": -300},
            "current": {"operating_cash_flow_usd_millions": 7200, "total_assets_usd_millions": 45000, "working_capital_contribution_usd_millions": -600},
            "source_ids": ["filing"],
            "methodology": "Use reported operating cash flow and signed working-capital cash contribution.",
        },
        "capital_investment": {
            "prior": {"depreciation_amortization_usd_millions": 1300, "capex_usd_millions": 1800},
            "current": {"depreciation_amortization_usd_millions": 1500, "capex_usd_millions": 2200},
            "source_ids": ["filing"],
            "methodology": "Compare reported depreciation and amortization with cash capital expenditures.",
        },
        "earnings_bridge": {
            "prior": bridge("prior"),
            "current": bridge("current"),
            "source_ids": ["filing"],
            "methodology": "Reconcile core operating profit, recurring below-line items, tax, adjustments, and diluted shares to EPS.",
        },
        "adjustments": adjustment_rows,
        "methodology": "Analyze earnings quality independently from valuation and reconcile every reported and adjusted figure.",
        "source_ids": ["filing"],
    }


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
            {"source_id": "tsmc_guidance", "source_type": "company_guidance", "organization": "TSMC", "observed_at": self.as_of, "locator": "capital budget guidance"},
            {"source_id": "micron_guidance", "source_type": "company_guidance", "organization": "Micron", "observed_at": self.as_of, "locator": "HBM investment guidance"},
            {"source_id": "supplier_call", "source_type": "earnings_transcript", "organization": "MKS Instruments", "observed_at": self.as_of, "locator": "quarterly earnings call"},
            {"source_id": "lrcx_call", "source_type": "earnings_transcript", "organization": "Lam Research", "observed_at": self.as_of, "locator": "quarterly earnings call"},
            {"source_id": "amat_prior_call", "source_type": "earnings_transcript", "organization": "AMAT", "observed_at": self.as_of, "locator": "prior-quarter earnings call transcript"},
            {"source_id": "amat_current_call", "source_type": "earnings_transcript", "organization": "AMAT", "observed_at": self.as_of, "locator": "current-quarter earnings call transcript"},
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

        def scenario(
            name, probability, scale, margin_delta, fixed_opex, growth_capex
        ):
            drivers = []
            for driver_id, segment_name, market_capex, share, gross_margin in [
                ("foundry_logic", "Foundry / Logic", 60000, 30, 50),
                ("dram", "DRAM", 30000, 25, 52),
                ("nand", "NAND", 20000, 20, 49),
                ("packaging_other", "Packaging / Other", 25000, 26, 54),
            ]:
                drivers.append({
                    "driver_id": driver_id,
                    "segment_name": segment_name,
                    "economic_driver": f"{segment_name} addressable equipment CAPEX",
                    "driver_value": market_capex * scale,
                    "driver_unit": "usd_millions",
                    "company_share_percent": share,
                    "revenue_conversion_factor": 1.0,
                    "timing_conversion_percent": 100.0,
                    "gross_margin_percent": gross_margin + margin_delta,
                    "source_ids": ["filing", "industry"],
                    "methodology": "Market CAPEX multiplied by AMAT share, equipment intensity, and shipment timing.",
                })
            return {
                "name": name,
                "probability": probability,
                "revenue_drivers": drivers,
                "operating_expenses": {
                    "fixed_usd_millions": fixed_opex,
                    "variable_percent_of_revenue": 2.0,
                    "source_ids": ["filing"],
                    "methodology": "Fixed R&D and SG&A plus a revenue-linked expense pool.",
                },
                "below_the_line": {
                    "net_interest_expense_usd_millions": 100,
                    "other_non_operating_income_usd_millions": 0,
                    "tax_rate_percent": 18.0,
                    "diluted_shares_millions": 800,
                    "source_ids": ["filing"],
                    "methodology": "Point-in-time capital structure and normalized effective tax rate.",
                },
                "cash_flow": {
                    "depreciation_amortization_usd_millions": 1500,
                    "stock_based_compensation_usd_millions": 600,
                    "change_in_net_working_capital_usd_millions": 500 * scale,
                    "other_operating_cash_adjustments_usd_millions": 0,
                    "maintenance_capex_usd_millions": 900,
                    "growth_capex_usd_millions": growth_capex,
                    "source_ids": ["filing"],
                    "methodology": "Net income bridged to OCF, with maintenance and growth CAPEX separated.",
                },
                "capital_efficiency": {
                    "prior_period": "FY2027",
                    "measurement_period_months": 12,
                    "prior_period_revenue_usd_millions": 33000,
                    "prior_period_operating_income_usd_millions": 8000,
                    "prior_period_invested_capital_usd_millions": 20000,
                    "ending_invested_capital_usd_millions": (
                        20000 + 900 + growth_capex - 1500 + 500 * scale
                    ),
                    "hurdle_rate_percent": 10.0,
                    "acquisition_investment_usd_millions": 0,
                    "other_invested_capital_change_usd_millions": 0,
                    "investment_lag_months": 24,
                    "source_ids": ["filing"],
                    "methodology": "Reconcile invested capital from CAPEX, depreciation, working capital, acquisitions, and other changes before comparing incremental NOPAT with the hurdle rate.",
                },
                "key_assumptions": [
                    "Market CAPEX, share, intensity, and timing are modeled separately.",
                    "Operating expenses and cash conversion follow the stated bridge.",
                ],
                "source_ids": ["filing", "industry"],
                "methodology": "Driver-based segment revenue, income statement, cash flow, and capital return model.",
            }
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
                    scenario("bear", 0.2, 0.85, -3.0, 7000, 1800),
                    scenario("base", 0.5, 1.0, 0.0, 7160, 1300),
                    scenario("bull", 0.3, 1.15, 2.0, 7350, 1300),
                ],
                "sensitivity_cases": [{
                    "sensitivity_id": "ai_capex_plus_10",
                    "label": "AI-related CAPEX +10%",
                    "driver_shocks": [
                        {"driver_id": "foundry_logic", "shock_percent": 10.0},
                        {"driver_id": "dram", "shock_percent": 10.0},
                        {"driver_id": "packaging_other", "shock_percent": 10.0},
                    ],
                    "source_ids": ["filing", "industry"],
                    "rationale": "Shock the AI-exposed demand pools while holding share, mix, timing, and cost assumptions constant.",
                }],
            },
            "earnings_quality": earnings_quality_payload(),
            "supply_chain_read_through": {
                "hypothesis": {
                    "hypothesis_id": "memory_and_foundry_capex_read_through",
                    "statement": "Independent customer CAPEX and supplier demand signals imply higher subject-company systems revenue, while competitor evidence provides a counter-test.",
                    "subject_metric": "Semiconductor Systems revenue",
                    "forecast_period": "FY2028",
                    "expected_direction": "increase",
                    "falsification_criteria": [
                        "Two major customers reduce the cited CAPEX programs.",
                        "Supplier bookings and subject-company backlog both decline.",
                    ],
                },
                "entities": [
                    {"entity_id": "amat", "name": "Applied Materials", "identifier": "AMAT", "role": "subject_company", "source_organization": "AMAT"},
                    {"entity_id": "tsmc", "name": "TSMC", "identifier": "TSM", "role": "customer", "source_organization": "TSMC"},
                    {"entity_id": "micron", "name": "Micron", "identifier": "MU", "role": "customer", "source_organization": "Micron"},
                    {"entity_id": "mks", "name": "MKS Instruments", "identifier": "MKSI", "role": "supplier", "source_organization": "MKS Instruments"},
                    {"entity_id": "lrcx", "name": "Lam Research", "identifier": "LRCX", "role": "competitor", "source_organization": "Lam Research"},
                ],
                "relationships": [
                    {"relationship_id": "tsmc_to_amat", "from_entity_id": "tsmc", "to_entity_id": "amat", "relationship_type": "customer_of", "upstream_metric": "Foundry CAPEX", "subject_metric": "Foundry / Logic systems revenue", "expected_lag_months": 12, "mechanism": "Customer fab investment creates addressable deposition and process-equipment demand after procurement and installation lags.", "source_ids": ["tsmc_guidance", "filing"]},
                    {"relationship_id": "micron_to_amat", "from_entity_id": "micron", "to_entity_id": "amat", "relationship_type": "customer_of", "upstream_metric": "HBM and DRAM CAPEX", "subject_metric": "DRAM systems revenue", "expected_lag_months": 9, "mechanism": "Memory capacity and technology-node investment increases addressable process-tool orders subject to share and timing.", "source_ids": ["micron_guidance", "filing"]},
                    {"relationship_id": "mks_to_amat", "from_entity_id": "mks", "to_entity_id": "amat", "relationship_type": "supplier_to", "upstream_metric": "Semiconductor component bookings", "subject_metric": "Equipment shipment capacity", "expected_lag_months": 6, "mechanism": "Upstream component bookings test whether equipment demand is propagating beyond the subject company's own disclosures.", "source_ids": ["supplier_call", "filing"]},
                    {"relationship_id": "lrcx_to_amat", "from_entity_id": "lrcx", "to_entity_id": "amat", "relationship_type": "competes_with", "upstream_metric": "Peer systems outlook", "subject_metric": "Industry demand and relative share", "expected_lag_months": 3, "mechanism": "A competitor's order outlook is an independent test of common demand and a counter-test for company-specific share assumptions.", "source_ids": ["lrcx_call", "filing"]},
                ],
                "signals": [
                    {"signal_id": "amat_backlog", "entity_id": "amat", "relationship_ids": ["micron_to_amat"], "metric": "Systems backlog", "period": "FY2028 outlook", "unit": "usd_millions", "prior_value": 10000, "current_value": 11000, "subject_metric_direction": "increase", "source_ids": ["filing"], "methodology": "Use the subject company's reported backlog as the non-independent anchor signal."},
                    {"signal_id": "tsmc_capex", "entity_id": "tsmc", "relationship_ids": ["tsmc_to_amat"], "metric": "Foundry CAPEX", "period": "FY2028 plan", "unit": "usd_millions", "prior_value": 30000, "current_value": 33000, "subject_metric_direction": "increase", "source_ids": ["tsmc_guidance"], "methodology": "Compare point-in-time company capital-budget guidance and map only the equipment-addressable portion through the documented relationship."},
                    {"signal_id": "micron_hbm_capex", "entity_id": "micron", "relationship_ids": ["micron_to_amat"], "metric": "HBM and DRAM CAPEX", "period": "FY2028 plan", "unit": "usd_millions", "prior_value": 8000, "current_value": 10000, "subject_metric_direction": "increase", "source_ids": ["micron_guidance"], "methodology": "Compare disclosed memory investment plans without treating total CAPEX as subject-company revenue."},
                    {"signal_id": "mks_bookings", "entity_id": "mks", "relationship_ids": ["mks_to_amat"], "metric": "Semiconductor component bookings", "period": "FY2028 run-rate", "unit": "usd_millions", "prior_value": 2000, "current_value": 2200, "subject_metric_direction": "increase", "source_ids": ["supplier_call"], "methodology": "Use supplier-reported bookings as an upstream capacity and demand confirmation signal."},
                    {"signal_id": "lrcx_outlook", "entity_id": "lrcx", "relationship_ids": ["lrcx_to_amat"], "metric": "Peer systems outlook", "period": "FY2028 outlook", "unit": "usd_millions", "prior_value": 11000, "current_value": 10500, "subject_metric_direction": "decrease", "source_ids": ["lrcx_call"], "methodology": "Retain the weaker peer outlook as counterevidence instead of averaging it away."},
                ],
                "coverage_rationale": "Cover two material customer demand pools, one upstream supplier, one direct competitor, and the subject-company anchor; each external signal uses that entity's primary disclosure.",
                "methodology": "Map numeric changes through explicit relationships and lags, count confirmation by distinct external entity and primary organization, and preserve counter-signals separately.",
                "source_ids": ["filing", "tsmc_guidance", "micron_guidance", "supplier_call", "lrcx_call"],
            },
            "earnings_call_diff": earnings_call_payload(
                as_of=self.as_of,
                prior_call_source="amat_prior_call",
                current_call_source="amat_current_call",
                primary_source="filing",
            ),
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
        self.assertAlmostEqual(
            earnings["base_vs_consensus_percent"],
            (earnings["scenarios"][1]["eps_usd"] / 9.5 - 1) * 100,
        )
        memo = render_focused_research_memo(dossier)
        headings = [memo.index(f"## {index}. {name}") for index, name in enumerate([
            "Investment Thesis", "Variant View", "Earnings Model", "Earnings Quality",
            "Supply-chain Read-through", "Earnings Call Diff / Management Calibration",
            "Valuation", "Catalyst Path",
            "Risk / Disconfirming Evidence", "Position Construction", "Score Summary",
        ], start=1)]
        self.assertEqual(headings, sorted(headings))
        self.assertGreater(memo.index("Investment Conviction"), memo.index("Position Construction"))
        self.assertIn("Base-case revenue drivers", memo)
        self.assertIn("Maintenance CAPEX", memo)
        self.assertIn("Incremental economics", memo)
        self.assertIn("invested-capital bridge", memo)
        self.assertIn("Growth CAPEX productivity", memo)
        self.assertIn("AI-related CAPEX +10%", memo)
        self.assertIn("GAAP to non-GAAP reconciliation", memo)
        self.assertIn("EPS growth attribution", memo)
        self.assertIn("Cross-company confirmation", memo)
        self.assertIn("Transmission map", memo)
        self.assertIn("Changed language and horizons", memo)
        self.assertIn("Eight-quarter guidance calibration", memo)
        self.assertIn("Prior commitment follow-through", memo)

    def test_driver_model_calculates_eps_cash_flow_roic_and_ai_capex_sensitivity(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        earnings = dossier["research_sections"]["earnings_model"]
        base = next(item for item in earnings["scenarios"] if item["name"] == "base")
        self.assertAlmostEqual(
            base["revenue_usd_millions"],
            sum(item["revenue_usd_millions"] for item in base["revenue_drivers"]),
        )
        expected_opex = (
            base["operating_expenses"]["fixed_usd_millions"]
            + base["revenue_usd_millions"]
            * base["operating_expenses"]["variable_percent_of_revenue"]
            / 100
        )
        self.assertAlmostEqual(base["operating_expense_usd_millions"], expected_opex)
        self.assertAlmostEqual(
            base["capex_usd_millions"],
            base["cash_flow"]["maintenance_capex_usd_millions"]
            + base["cash_flow"]["growth_capex_usd_millions"],
        )
        self.assertAlmostEqual(
            base["free_cash_flow_usd_millions"],
            base["operating_cash_flow_usd_millions"] - base["capex_usd_millions"],
        )
        economics = base["incremental_economics"]
        bridge = economics["invested_capital_bridge"]
        self.assertAlmostEqual(
            bridge["reported_ending_invested_capital_usd_millions"],
            bridge["beginning_invested_capital_usd_millions"]
            + bridge["maintenance_capex_usd_millions"]
            + bridge["growth_capex_usd_millions"]
            - bridge["less_depreciation_amortization_usd_millions"]
            + bridge["net_working_capital_investment_usd_millions"]
            + bridge["acquisition_investment_usd_millions"]
            + bridge["other_invested_capital_change_usd_millions"],
        )
        self.assertAlmostEqual(
            economics["incremental_roic_percent"],
            economics["incremental_nopat_usd_millions"]
            / economics["incremental_invested_capital_usd_millions"]
            * 100,
        )
        self.assertAlmostEqual(
            economics["incremental_economic_profit_usd_millions"],
            economics["incremental_nopat_usd_millions"]
            - economics["incremental_invested_capital_usd_millions"]
            * economics["hurdle_rate_percent"]
            / 100,
        )
        self.assertEqual(economics["state"], "value_creating_above_hurdle")
        self.assertGreater(economics["growth_capex_revenue_multiple"], 0)
        self.assertGreater(economics["growth_capex_operating_income_multiple"], 0)
        sensitivity = earnings["sensitivity_cases"][0]
        self.assertEqual(sensitivity["label"], "AI-related CAPEX +10%")
        self.assertGreater(sensitivity["results"]["revenue_change_percent"], 0)
        self.assertGreater(sensitivity["results"]["eps_change_percent"], 0)
        self.assertGreater(sensitivity["results"]["free_cash_flow_change_percent"], 0)
        self.assertGreater(
            sensitivity["results"]["incremental_roic_change_basis_points"], 0
        )

    def test_rejects_missing_driver_or_cash_flow_model(self):
        no_drivers = copy.deepcopy(self.payload)
        no_drivers["earnings_model"]["scenarios"][1].pop("revenue_drivers")
        with self.assertRaisesRegex(ValueError, "revenue drivers"):
            build_focused_research_dossier(
                no_drivers, policy=self.policy, code_revision="a" * 40
            )
        no_cash_flow = copy.deepcopy(self.payload)
        no_cash_flow["earnings_model"]["scenarios"][1].pop("cash_flow")
        with self.assertRaisesRegex(ValueError, "cash_flow"):
            build_focused_research_dossier(
                no_cash_flow, policy=self.policy, code_revision="a" * 40
            )

    def test_rejects_unreconciled_invested_capital_bridge(self):
        invalid = copy.deepcopy(self.payload)
        invalid["earnings_model"]["scenarios"][1]["capital_efficiency"][
            "ending_invested_capital_usd_millions"
        ] += 1
        with self.assertRaisesRegex(ValueError, "invested capital bridge"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_validator_recalculates_driver_outputs_instead_of_trusting_them(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        dossier["research_sections"]["earnings_model"]["scenarios"][1][
            "revenue_drivers"
        ][0]["revenue_usd_millions"] += 1
        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            validate_focused_research_dossier(
                dossier, as_of_date=self.as_of, maximum_age_days=14
            )

    def test_validator_recalculates_incremental_economics_outputs(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        dossier["research_sections"]["earnings_model"]["scenarios"][1][
            "incremental_economics"
        ]["incremental_economic_profit_usd_millions"] += 1
        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            validate_focused_research_dossier(
                dossier, as_of_date=self.as_of, maximum_age_days=14
            )

    def test_earnings_quality_reconciles_growth_cash_adjustments_and_buybacks(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        quality = dossier["research_sections"]["earnings_quality"]
        growth = quality["balance_sheet_growth"]
        self.assertAlmostEqual(growth["revenue_growth_percent"], 20.0)
        self.assertAlmostEqual(
            growth["accounts_receivable"][
                "minus_revenue_growth_percentage_points"
            ],
            10.0,
        )
        self.assertAlmostEqual(
            growth["inventory"]["minus_revenue_growth_percentage_points"],
            10.0,
        )
        self.assertEqual(len(quality["adjustments"]), 5)
        self.assertEqual(
            {item["adjustment_id"] for item in quality["adjustments"]},
            {
                "stock_based_compensation",
                "restructuring",
                "acquisition_adjustment",
                "tax_benefit",
                "one_off_gain",
            },
        )
        self.assertAlmostEqual(
            quality["accruals_and_cash_conversion"][
                "working_capital_contribution_usd_millions"
            ],
            -600,
        )
        self.assertLess(
            quality["capital_intensity"][
                "current_depreciation_to_capex_percent"
            ],
            100,
        )
        self.assertEqual(quality["eps_growth_attribution"]["state"], "reconciled")
        self.assertAlmostEqual(
            quality["eps_growth_attribution"][
                "reconciled_total_percentage_points"
            ],
            quality["eps_growth_attribution"]["reported_eps_growth_percent"],
        )
        self.assertGreater(
            quality["eps_growth_attribution"][
                "share_repurchase_contribution_percentage_points"
            ],
            0,
        )

    def test_rejects_unreconciled_gaap_non_gaap_bridge(self):
        invalid = copy.deepcopy(self.payload)
        invalid["earnings_quality"]["earnings_bridge"]["current"][
            "reported_gaap_net_income_usd_millions"
        ] += 1
        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_rejects_each_missing_required_earnings_quality_adjustment(self):
        for adjustment_id in (
            "stock_based_compensation",
            "restructuring",
            "acquisition_adjustment",
            "tax_benefit",
            "one_off_gain",
        ):
            invalid = copy.deepcopy(self.payload)
            invalid["earnings_quality"]["adjustments"] = [
                item
                for item in invalid["earnings_quality"]["adjustments"]
                if item["adjustment_id"] != adjustment_id
            ]
            with self.subTest(adjustment_id=adjustment_id), self.assertRaisesRegex(
                ValueError, "adjustments are incomplete"
            ):
                build_focused_research_dossier(
                    invalid, policy=self.policy, code_revision="a" * 40
                )

    def test_validator_recalculates_earnings_quality_outputs(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        dossier["research_sections"]["earnings_quality"][
            "accruals_and_cash_conversion"
        ]["cash_conversion_percent"] += 1
        with self.assertRaisesRegex(ValueError, "earnings quality.*does not reconcile"):
            validate_focused_research_dossier(
                dossier, as_of_date=self.as_of, maximum_age_days=14
            )

    def test_supply_chain_requires_independent_primary_cross_company_confirmation(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        supply_chain = dossier["research_sections"]["supply_chain_read_through"]
        confirmation = supply_chain["cross_company_confirmation"]
        self.assertEqual(confirmation["state"], "confirmed_with_counter_signals")
        self.assertEqual(confirmation["external_entity_count"], 4)
        self.assertEqual(confirmation["independent_primary_organization_count"], 4)
        self.assertEqual(len(confirmation["supporting_external_entity_ids"]), 3)
        self.assertEqual(confirmation["signal_counts"]["contradicting"], 1)
        self.assertEqual(
            confirmation["external_signal_counts"]["contradicting"], 1
        )
        self.assertFalse(confirmation["score_used"])

    def test_rejects_supply_chain_signal_without_entity_primary_source(self):
        invalid = copy.deepcopy(self.payload)
        signal = next(
            item
            for item in invalid["supply_chain_read_through"]["signals"]
            if item["signal_id"] == "tsmc_capex"
        )
        signal["source_ids"] = ["industry"]
        with self.assertRaisesRegex(ValueError, "lacks its entity's primary source"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_rejects_supply_chain_relationship_that_conflicts_with_entity_roles(self):
        invalid = copy.deepcopy(self.payload)
        relationship = next(
            item
            for item in invalid["supply_chain_read_through"]["relationships"]
            if item["relationship_id"] == "tsmc_to_amat"
        )
        relationship["relationship_type"] = "supplier_to"
        with self.assertRaisesRegex(ValueError, "conflicts with entity roles"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_buy_requires_three_supporting_external_entities(self):
        invalid = copy.deepcopy(self.payload)
        signal = next(
            item
            for item in invalid["supply_chain_read_through"]["signals"]
            if item["signal_id"] == "mks_bookings"
        )
        signal["subject_metric_direction"] = "decrease"
        with self.assertRaisesRegex(ValueError, "cross-company confirmation"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_validator_recalculates_supply_chain_outputs(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        dossier["research_sections"]["supply_chain_read_through"]["signals"][0][
            "change_percent"
        ] += 1
        with self.assertRaisesRegex(ValueError, "supply-chain.*does not reconcile"):
            validate_focused_research_dossier(
                dossier, as_of_date=self.as_of, maximum_age_days=14
            )

    def test_earnings_call_diffs_language_questions_guidance_and_promises(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        call = dossier["research_sections"]["earnings_call_diff"]
        language = call["language_diff"]
        self.assertEqual(language["new_topic_ids"], ["advanced_packaging"])
        self.assertEqual(language["removed_topic_ids"], ["margin_progress"])
        capacity = next(
            item for item in language["changed_topics"]
            if item["topic_id"] == "capacity_constraint"
        )
        self.assertTrue(capacity["horizon_changed"])
        self.assertIn("2027", capacity["added_terms"])
        self.assertIn("2026", capacity["removed_terms"])
        self.assertGreater(call["management_confidence"]["change"], 0)
        questions = call["analyst_question_diff"]
        self.assertEqual(questions["new_theme_ids"], ["growth_capex_returns"])
        self.assertEqual(questions["newly_evaded_theme_ids"], ["growth_capex_returns"])
        self.assertEqual(
            [item["theme_id"] for item in questions["changed_recurring_themes"]],
            ["backlog_conversion"],
        )
        eps_guidance = next(
            item for item in call["numeric_guidance_diff"]["changes"]
            if item["guidance_id"] == "quarterly_eps"
        )
        self.assertEqual(eps_guidance["range_state"], "narrowed")
        calibration = call["management_guidance_calibration"]
        self.assertEqual(calibration["quarter_count"], 8)
        self.assertEqual(calibration["bias_classification"], "historically_conservative")
        self.assertFalse(calibration["score_used"])
        commitments = call["prior_commitment_follow_through"]
        self.assertEqual(commitments["status_counts"]["met"], 1)
        self.assertEqual(commitments["status_counts"]["missed"], 1)
        self.assertEqual(commitments["status_counts"]["pending"], 1)
        self.assertAlmostEqual(commitments["resolved_fulfillment_rate_percent"], 50)

    def test_earnings_call_requires_subject_company_transcript(self):
        invalid = copy.deepcopy(self.payload)
        invalid["earnings_call_diff"]["prior_call"]["source_ids"] = ["filing"]
        with self.assertRaisesRegex(ValueError, "subject company's earnings transcript"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_management_calibration_requires_exactly_eight_quarters(self):
        invalid = copy.deepcopy(self.payload)
        invalid["earnings_call_diff"]["guidance_history"].pop()
        with self.assertRaisesRegex(ValueError, "exactly eight quarters"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_earnings_call_date_cannot_postdate_its_transcript_source(self):
        invalid = copy.deepcopy(self.payload)
        invalid["earnings_call_diff"]["current_call"]["call_date"] = "2026-08-21"
        with self.assertRaisesRegex(ValueError, "later than its source observation"):
            build_focused_research_dossier(
                invalid, policy=self.policy, code_revision="a" * 40
            )

    def test_validator_recalculates_earnings_call_outputs(self):
        dossier = build_focused_research_dossier(
            self.payload, policy=self.policy, code_revision="a" * 40
        )
        dossier["research_sections"]["earnings_call_diff"][
            "management_guidance_calibration"
        ]["mean_absolute_midpoint_error_percent"] += 1
        with self.assertRaisesRegex(ValueError, "earnings-call.*does not reconcile"):
            validate_focused_research_dossier(
                dossier, as_of_date=self.as_of, maximum_age_days=14
            )

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
            "earnings_quality",
            "supply_chain_read_through",
            "earnings_call_diff",
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
